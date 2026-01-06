# NCE Hooks Implementation Guide for Eden Emulator

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Implementation Details](#implementation-details)
4. [Challenges and Solutions](#challenges-and-solutions)
5. [Low-Level Details](#low-level-details)
6. [Debugging Guide](#debugging-guide)
7. [Files Reference](#files-reference)
8. [Future Improvements](#future-improvements)

---

## Overview

### What is NCE?
NCE (Native Code Execution) is an ARM64 execution backend for Yuzu/Eden on Android that runs game code natively on the host ARM64 processor instead of using a JIT recompiler (Dynarmic). This provides better performance but makes hooking more complex.

### What are NCE Hooks?
NCE hooks allow intercepting game code at specific addresses when Eden is using the Native Code Execution CPU backend. The addresses are located simply by adding offsets to a base address. This differs from Dynarmic, whose addresses needs to be located by hooking the JIT compiler to figure out which host address corresponds with each guest address. Although it's simpler to find the target addresses with the NCE backend, there is much friction between hooks and Eden caused by the NCE developers' attempts to maximize speed at all costs.

### Source Files Structure
```
scripts/                              # JavaScript hooks (reference)
├── libYuzu.js                        # Core JS hooking framework
└── NS_<TitleID>_<GameName>.js        # Game-specific JS hooks

src/core/arm/nce/                     # NCE implementation
├── arm_nce.h / arm_nce.cpp           # NCE ARM interface & instruction emulation
├── arm_nce.s                         # Assembly signal handlers
├── nce_hooks.h / nce_hooks.cpp       # Hook system & External C API for Frida
└── guest_context.h                   # Guest CPU state structure
```

### External C API for Frida

Eden exports the following functions for Frida/libYuzu.js to use:

| Function | Signature | Purpose |
|----------|-----------|---------|
| `NceInstallExternalHook` | `bool (u64 address, u32 expected_inst)` | Install a hook at address |
| `NceRemoveExternalHook` | `bool (u64 address)` | Remove a previously installed hook |
| `NceTrampoline` | `void (u64 pc, void* context)` | Empty function Frida intercepts |
| `NceGetCurrentContext` | `void* ()` | Get current GuestContext pointer |
| `NceRegisterLogCallback` | `void (NceLogCallback callback)` | Register callback to receive Eden logs |
| `NceClearAllHooks` | `void ()` | Clear all hooks and free trampoline memory |
**Usage from libYuzu.js:**
```javascript
const NceInstallExternalHook = new NativeFunction(
    Module.getExportByName(null, 'NceInstallExternalHook'), 'bool', ['uint64', 'uint32']);
const nceTrampoline = Module.getExportByName(null, 'NceTrampoline');

// Base address is found by scanning for MOD0 header in libYuzu.js
const mod0Base = Memory.scanSync(/* ... */);

// Calculate host address from Ghidra address
// Ghidra shows "guest" addresses (Switch virtual addresses)
// We convert to "host" addresses (Eden process memory)
const ghidraAddr = 0x80064ab8;
const hostAddr = mod0Base.add(ghidraAddr - 0x80004000);

// Install hook at the host address
// 0 = skip instruction verification
const installResult = NceInstallExternalHook(uint64(hostAddr.toString()), 0);

// Intercept the trampoline to handle hooks in JavaScript
Interceptor.attach(nceTrampoline, {
    onEnter(args) {
        const pc = args[0];         // Program counter (host address)
        const context = args[1];    // GuestContext pointer

        // Read registers: X0 at offset 0, X1 at offset 8, etc.
        const x0 = context.readPointer();
        const x1 = context.add(0x8).readPointer();
        const text = x1.readUtf8String();
    }
});
```

---

## Architecture

### How NCE Hooks Work

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           HOOK INSTALLATION                             │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Game loads, Frida/libYuzu.js attaches to process                    │
│  2. Frida scans memory for MOD0 header to find base address             │
│  3. Calculate hook address: base + (ghidra_offset - 0x80004000)         │
│  4. Call NceInstallExternalHook() which (lazy init on first call):      │
│     a. Saves original instruction                                       │
│     b. Creates native execution trampoline (if not PC-relative)         │
│     c. Replaces instruction with UDF #0 (0x00000000)                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           HOOK EXECUTION                                │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Guest executes UDF #0 → CPU raises SIGILL                           │
│  2. arm_nce.s: GuestIllegalInstructionSignalHandler catches signal      │
│  3. arm_nce.cpp: HandleGuestIllegalInstruction() is called              │
│  4. Look up hook callback by PC address                                 │
│  5. Call NceTrampoline() → Frida intercepts and runs JS handler         │
│  6. Check for native trampoline via NceHooks::GetTrampoline()           │
│     - If found: redirect PC to trampoline (native execution)            │
│     - If not found: emulate via MatchAndExecuteOneInstruction()         │
│  7. Return to guest execution                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     NATIVE EXECUTION TRAMPOLINE                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Trampoline buffer (24 bytes, executable memory):                       │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ Offset 0:  original_instruction  (e.g., CMP W22, W8)             │   │
│  │ Offset 4:  LDR X16, #12          (load return address)           │   │
│  │ Offset 8:  BR X16                (jump to return address)        │   │
│  │ Offset 12: padding               (alignment)                     │   │
│  │ Offset 16: return_address        (hook_addr + 4, 64-bit)         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│  Execution: Run original instruction → Jump back to game code           │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Data Structures

#### GuestContext (from `guest_context.h`)
```cpp
struct GuestContext {
    std::array<u64, 31> cpu_registers{};  // X0-X30
    u64 sp{};                              // Stack Pointer (X31)
    u64 pc{};                              // Program Counter
    u32 fpcr{};                            // Floating-point Control Register
    u32 fpsr{};                            // Floating-point Status Register
    std::array<u128, 32> vector_registers{}; // V0-V31
    u32 pstate{};                          // Process State (includes NZCV flags)
    // ... host context, TLS pointers, etc.
    System* system{};                      // Pointer to emulator system
};
```

#### GuestContext Offsets for Frida

When reading registers from `GuestContext*` in JavaScript:
```javascript
// context = args[1] from NceTrampoline
const x0 = context.readU64();           // Offset 0x00
const x1 = context.add(0x08).readU64(); // Offset 0x08
const x2 = context.add(0x10).readU64(); // Offset 0x10
// ... X0-X30 at offsets 0x00-0xF0 (8 bytes each)
const sp = context.add(0xF8).readU64(); // Offset 0xF8 (Stack Pointer)
const pc = context.add(0x100).readU64(); // Offset 0x100 (Program Counter)
```

### Address Calculation Formula

```
host_address = mod0_base + (ghidra_address - 0x80004000)
```

Where:
- `mod0_base`: Module base address found by scanning for `MOD0` magic header (in host memory)
- `ghidra_address`: Address shown in Ghidra (e.g., `0x8002ad60`) - the Switch virtual address
- `0x80004000`: Ghidra's default base address for Switch NSO files
- `host_address`: The actual address in Eden's process memory (e.g., `0x18bec6bd4c`)

**Terminology:**
- **Ghidra/Guest address**: The address as seen in Ghidra/IDA (Switch virtual address space)
- **Host address**: The address in Eden's process memory where the code actually resides

The `MOD0` header is a standard structure in Switch executables that libYuzu.js scans for to reliably find the base address.

---

## Implementation Details

### Instruction Emulation

NCE hooks use two different execution strategies depending on the instruction type:

1. **Native Execution Trampolines** (most instructions): The original instruction is copied to executable memory and run natively. This provides 100% compatibility.

2. **Software Emulation** (PC-relative instructions only): Instructions that calculate addresses based on PC cannot be relocated, so they are emulated using Dynarmic's battle-tested decoder.

#### PC-Relative Instruction Emulation (`interpreter_visitor.cpp`)

When a hook is installed on a PC-relative instruction, `MatchAndExecuteOneInstruction()` emulates it using the `InterpreterVisitor` class with Dynarmic's instruction decoder. This approach was chosen for **runtime stability** over manual bit decoding because:

- Dynarmic's `Decode<>()` is battle-tested across multiple emulator projects
- `Imm<N>::SignExtend<>()` and `concatenate()` are proven correct
- Memory access via `Core::Memory::Memory` handles guest↔host address translation correctly
- Returns `std::nullopt` on failure, allowing graceful fallback

**Supported PC-relative instructions:**

| Instruction | Purpose | Notes |
|-------------|---------|-------|
| `ADR` | PC-relative address | Loads PC + offset into register |
| `ADRP` | PC-relative page address | Loads page-aligned PC + offset |
| `B` | Unconditional branch | PC + signed offset |
| `BL` | Branch with link | Sets LR, then branches |
| `B.cond` | Conditional branch | Evaluates NZCV flags from pstate |
| `CBZ` | Compare and branch if zero | 32/64-bit comparison |
| `CBNZ` | Compare and branch if not zero | 32/64-bit comparison |
| `TBZ` | Test bit and branch if zero | Tests single bit (0-63) |
| `TBNZ` | Test bit and branch if not zero | Tests single bit (0-63) |
| `LDR (literal)` | Load from PC-relative address | Already in upstream |
| `LDRSW (literal)` | Load signed word from PC-relative | Sign-extends to 64-bit |
| `PRFM (literal)` | Prefetch from PC-relative | No-op in emulation |

**How it works:**

```cpp
// In HandleHookIfInstalled() when no trampoline exists:
auto& memory = guest_ctx->system->ApplicationMemory();
auto next_pc = MatchAndExecuteOneInstruction(memory, &host_ctx, fpctx);
if (next_pc) {
    host_ctx.pc = *next_pc;
    guest_ctx->pc = *next_pc;
    return true;
}
```

The `InterpreterVisitor` now includes:
- Optional `pstate` pointer for condition flag evaluation (N, Z, C, V)
- `m_next_pc` optional to return computed branch targets
- `GetNextPc()` method to retrieve branch target or fall back to PC + 4

### Native Execution Trampolines (`nce_hooks.cpp`)

Instead of emulating every hooked instruction in software, most instructions are executed **natively** using a trampoline system. This provides 100% compatibility with all ARM64 instructions except PC-relative ones.

#### How Trampolines Work

1. **At hook installation:** If the original instruction is NOT PC-relative, a 24-byte trampoline is allocated in executable memory.

2. **Trampoline structure:**
```cpp
struct TrampolineBlock {
    u32 original_instruction;  // The instruction we moved here
    u32 ldr_x16;               // LDR X16, #12 (0x58000070)
    u32 br_x16;                // BR X16 (0xD61F0200)
    u32 padding;               // Alignment
    u64 return_address;        // hook_addr + 4
};
```

3. **At hook trigger:** After the Frida callback runs, the signal handler redirects execution to the trampoline instead of emulating.

4. **Native execution:** The CPU executes the original instruction, then jumps back to the game code.

#### PC-Relative Instructions

Instructions that use PC-relative addressing cannot be moved to a trampoline because they calculate addresses based on their location:

```cpp
bool IsPcRelative(u32 inst) {
    // B / BL - Branch immediate
    if ((inst & 0x7C000000) == 0x14000000) return true;
    // B.cond - Conditional branch
    if ((inst & 0xFF000010) == 0x54000000) return true;
    // CBZ / CBNZ - Compare and branch
    if ((inst & 0x7E000000) == 0x34000000) return true;
    // TBZ / TBNZ - Test and branch
    if ((inst & 0x7E000000) == 0x36000000) return true;
    // ADR / ADRP - PC-relative address
    if ((inst & 0x1F000000) == 0x10000000) return true;
    // LDR (literal) - PC-relative load
    if ((inst & 0x3B000000) == 0x18000000) return true;
    // PRFM (literal)
    if ((inst & 0xFF000000) == 0xD8000000) return true;
    return false;
}
```

These instructions are emulated via `MatchAndExecuteOneInstruction()` using the `InterpreterVisitor` class, which leverages Dynarmic's proven instruction decoder.

#### Signal-Safe Trampoline Lookup

The trampoline lookup table uses atomic operations for thread safety without locks:
```cpp
static TrampolineEntry s_trampolines[MaxTrampolines];
static std::atomic<size_t> s_trampoline_count{0};

u64 GetTrampoline(u64 address) {
    // Signal-safe: no locks, just atomic read
    const size_t count = s_trampoline_count.load(std::memory_order_acquire);
    for (size_t i = 0; i < count; ++i) {
        if (s_trampolines[i].address == address) {
            return s_trampolines[i].trampoline_address;
        }
    }
    return 0;
}
```

---

## Challenges and Solutions

### Challenge 1: Finding the Correct Base Address

**Problem:** The NSO header isn't loaded into memory, so we can't scan for `NSO0` magic.

**Solution:** ✓ Scan for `MOD0` header instead. The `MOD0` structure is present in all Switch executables and its magic bytes can be found by scanning memory. For more information, see https://switchbrew.org/wiki/Rtld.

### Challenge 2: Inline Hooks (Non-Function-Start)

**Problem:** Many hooks target addresses in the middle of functions, not at function starts.

**Solution:** Provide the expected instruction for verification when calling `NceInstallExternalHook()`:
```javascript
// Read instruction at hook address before installing
const instruction = hookAddr.readU32();
if (instruction !== 0x710006FF) {
    console.log('Instruction mismatch - wrong game version or base address');
    return;
}
NceInstallExternalHook(uint64(hookAddr.toString()), instruction);
```

### Challenge 3: PC-Relative Instruction Emulation

**Problem:** When a hook is installed on a PC-relative instruction, we cannot use a native trampoline because the instruction calculates addresses based on its PC location. Moving it would produce wrong results.

**Symptoms (before fix):**
- `Hook at XXXX has no trampoline, instruction XXXX may not execute correctly`
- Game logic errors (wrong branch taken, wrong address loaded)

**Solution:** ✓ Implemented full PC-relative instruction emulation in `interpreter_visitor.cpp` using Dynarmic's battle-tested decoder. All common PC-relative instructions are now supported:
- Address generation: `ADR`, `ADRP`
- Unconditional branches: `B`, `BL`
- Conditional branches: `B.cond` (with NZCV flag evaluation)
- Compare and branch: `CBZ`, `CBNZ`
- Test and branch: `TBZ`, `TBNZ`
- Literal loads: `LDR (literal)`, `LDRSW (literal)`, `LDR SIMD (literal)`
- Prefetch: `PRFM (literal)` (no-op)

**Design decision:** We chose to extend `InterpreterVisitor` rather than manual bit decoding for **runtime stability**. The slight increase in merge conflict potential with upstream is acceptable because Dynarmic's decoder is thoroughly tested.

### Challenge 4: Flag Calculation Errors

**Problem:** Instructions like `CMP` and `SUBS` set NZCV flags. If calculated incorrectly, subsequent conditional branches fail.

**Solution:** Carefully implement flag calculation:
```cpp
u32 nzcv = 0;
if (result == 0) nzcv |= 0x40000000;                              // Z (Zero)
if (result & sign_bit) nzcv |= 0x80000000;                        // N (Negative)
if (op1 >= op2) nzcv |= 0x20000000;                               // C (Carry/No Borrow)
if (((op1 ^ op2) & (op1 ^ result)) & sign_bit) nzcv |= 0x10000000; // V (Overflow)

host_ctx->pstate = (host_ctx->pstate & ~0xF0000000) | nzcv;
```

### Challenge 5: Compiler Optimizations Breaking Frida Interception

**Problem:** The `NceTrampoline` function is nearly empty, so the compiler may:
- Inline it (no actual function call)
- Optimize away "unused" parameters (Frida sees garbage in args)

**Symptoms:**
- Frida's `onEnter` never triggers even though logcat shows the function ran
- `args[0]` or `args[1]` contain wrong values

**Solution:** Use compiler attributes and inline assembly:
```cpp
// Prevent inlining - Frida needs a real function call to intercept
__attribute__((noinline)) void NceTrampoline(u64 pc, void* context_ptr) {
    // Force compiler to treat parameters as live
    // Without this, optimizer may reuse X0/X1 registers before the call
    asm volatile("" :: "r"(pc), "r"(context_ptr) : "memory");
}
```

### Challenge 6: Trampoline Address Calculation

**Problem:** The `LDR X16, #offset` instruction in trampolines must load the return address from the correct offset.

**Symptoms:**
- Game freezes after hook triggers
- `Unmapped InvalidateNCE` errors
- `vtable bouncing` spam

**Root Cause:** ARM64's PC-relative `LDR` calculates: `address = PC + offset`. If offset is wrong, it loads garbage instead of the return address.

**Solution:** Calculate offset from the instruction's position, not the start of the block:
```cpp
// LDR X16, #12 at offset 4 → loads from PC+12 = offset 16 (return_address)
// Encoding: 0x58000070
block->ldr_x16 = 0x58000070;
```

### Challenge 7: Frida Interception Doesn't Work Inside NativeFunction Calls

**Problem:** `Interceptor.attach` and `Interceptor.replace` do not fire for functions called from within a `NativeFunction` call context.

**Symptoms:**
- `NceLog` calls from `NceTrampoline` (signal handler) appear in Frida ✓
- `NceLog` calls from `NceInstallExternalHook` (called via `NativeFunction`) do NOT appear ✗

**Root Cause:** When JavaScript calls a native function via Frida's `NativeFunction`, Frida's internal trampolines conflict with interception of functions called within that context. This is a fundamental Frida limitation.

**Solution:** Use callback registration instead of interception. Eden calls a function pointer that Frida registers:

```cpp
// Eden C++ side
static std::atomic<NceLogCallback> s_log_callback{nullptr};

void NceRegisterLogCallback(NceLogCallback callback) {
    s_log_callback.store(callback, std::memory_order_release);
}

void NceLog(int level, const char* message) {
    auto cb = s_log_callback.load(std::memory_order_acquire);
    if (cb) {
        cb(level, message);  // Direct call - no Frida interception needed
    }
}
```

```javascript
// Frida side - register callback early
const logCallback = new NativeCallback((level, messagePtr) => {
    const message = messagePtr.readCString();
    console.log(`[Eden] ${message}`);
}, 'void', ['int', 'pointer']);

// IMPORTANT: Keep reference to prevent GC
globalThis._nceLogCallback = logCallback;

const registerFn = new NativeFunction(
    Module.getExportByName(null, 'NceRegisterLogCallback'), 'void', ['pointer']);
registerFn(logCallback);
```

**Key insight:** Direct function pointer calls work in all contexts; Frida interception does not.

---

## Low-Level Details

### Assembly Signal Handler (`arm_nce.s`)

The NCE backend uses Linux signals to handle guest exceptions. When `UDF #0` executes, it triggers SIGILL which is caught by `GuestIllegalInstructionSignalHandler`.

#### TLS Structure

The signal handler uses a Thread-Local Storage (TLS) structure pointed to by `tpidr_el0`:
```cpp
#define TpidrEl0TlsMagic        0x00  // Magic value (identifies guest threads)
#define TpidrEl0NativeContext   0x08  // Pointer to GuestContext
#define TpidrEl0Lock            0x10  // Spinlock for thread synchronization
```

#### Signal Handler Flow

```
GuestIllegalInstructionSignalHandler(sig, info, raw_context)
                    │
                    ▼
        ┌───────────────────────┐
        │ Read tpidr_el0 → x8   │
        │ Load TlsMagic → w9    │
        └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ Compare w9 to TlsMagic│
        │ (Is this a guest      │
        │  thread?)             │
        └───────────────────────┘
                    │
           ┌────────┴────────┐
           │ No              │ Yes
           ▼                 ▼
     ┌──────────┐    ┌───────────────────┐
     │ Label 3: │    │ Load native_ctx   │
     │ Host     │    │ from TLS → x9     │
     │ fault    │    └───────────────────┘
     └──────────┘            │
           │                 ▼
           │        ┌───────────────────┐
           │        │ Is native_ctx     │
           │        │ NULL?             │
           │        └───────────────────┘
           │                 │
           │        ┌────────┴────────┐
           │        │ Yes             │ No
           │        ▼                 ▼
           │   ┌──────────┐    ┌──────────┐
           └──►│ Label 3: │    │ Label 1: │
               │ Host     │    │ Guest    │
               │ fault    │    │ handler  │
               └──────────┘    └──────────┘
```

#### Complete Handler Code with Annotations

```asm
_ZN4Core6ArmNce36GuestIllegalInstructionSignalHandlerEiPvS1_:
    /* ═══════════════════════════════════════════════════════════════
     * PHASE 1: Determine if this is a guest or host fault
     * ═══════════════════════════════════════════════════════════════ */
    
    /* Read current thread's TLS pointer */
    mrs     x8, tpidr_el0
    
    /* Load the magic value from TLS offset 0x00 */
    ldr     w9, [x8, #(TpidrEl0TlsMagic)]

    /* Load expected magic constant into w10 */
    LOAD_IMMEDIATE_32(w10, TlsMagic)

    /* Compare: is this a guest thread? */
    cmp     w9, w10
    b.ne    3f              /* If magic doesn't match → host fault */

    /* ═══════════════════════════════════════════════════════════════
     * PHASE 1.5: NULL check for Frida compatibility (ADDED)
     * ═══════════════════════════════════════════════════════════════ */
    
    /* Load native_context pointer from TLS offset 0x08 */
    ldr     x9, [x8, #(TpidrEl0NativeContext)]
    
    /* If native_context is NULL, treat as host fault */
    /* This happens when Frida spawns threads that have TLS magic */
    /* but no valid GuestContext - without this check, we'd crash */
    cbz     x9, 3f

1:
    /* ═══════════════════════════════════════════════════════════════
     * PHASE 2: Guest handler - switch to host context
     * ═══════════════════════════════════════════════════════════════ */
    
    /* Set up stack frame */
    stp     x29, x30, [sp, #-0x20]!
    str     x19, [sp, #0x10]
    mov     x29, sp

    /* Save guest tpidr_el0 for later restoration */
    mov     x19, x8

    /* Load GuestContext pointer into x0 (first argument) */
    ldr     x0, [x8, #(TpidrEl0NativeContext)]
    
    /* Load saved host tpidr_el0 from GuestContext.host_context */
    ldr     x3, [x0, #(GuestContextHostContext + HostContextTpidrEl0)]
    
    /* CRITICAL: Switch from guest TLS to host TLS */
    /* This allows C++ code to use thread_local variables correctly */
    msr     tpidr_el0, x3

    /* ═══════════════════════════════════════════════════════════════
     * PHASE 3: Call C++ handler
     * ═══════════════════════════════════════════════════════════════ */
    
    /* x0 = GuestContext*, x1 = siginfo_t*, x2 = ucontext_t* */
    /* Already set: x0 from above, x1/x2 passed through from signal */
    bl      _ZN4Core6ArmNce29HandleGuestIllegalInstructionEPNS_12GuestContextEPvS3_
    
    /* ═══════════════════════════════════════════════════════════════
     * PHASE 4: Determine whether to return to guest or host
     * ═══════════════════════════════════════════════════════════════ */
    
    /* Handler returns bool: true = continue guest, false = exit to host */
    cbz     x0, 2f          /* If false, keep host tpidr_el0 */

    /* Otherwise, restore guest tpidr_el0 for continued execution */
    msr     tpidr_el0, x19

2:
    /* ═══════════════════════════════════════════════════════════════
     * PHASE 5: Clean up and return
     * ═══════════════════════════════════════════════════════════════ */
    
    ldr     x19, [sp, #0x10]
    ldp     x29, x30, [sp], #0x20
    ret
    /* Returning from signal handler resumes execution at modified PC */

3:
    /* ═══════════════════════════════════════════════════════════════
     * HOST FAULT PATH: Not a guest thread, or invalid context
     * ═══════════════════════════════════════════════════════════════ */
    
    /* Tail call to host fault handler (preserves all arguments) */
    b       _ZN4Core6ArmNce28HandleHostIllegalInstructionEiPvS1_
```

#### Why the NULL Check Was Added

The original NCE code only checked for `TlsMagic` to determine if a SIGILL came from guest code. However, when Frida attaches to the process:

1. **Frida creates internal threads** for its agent JavaScript runtime
2. These threads may **inherit or copy TLS structures** that contain valid `TlsMagic`
3. But they have **no valid `GuestContext`** (native_context is NULL or garbage)
4. Without the NULL check, the handler would:
   - Pass NULL to `HandleGuestIllegalInstruction()`
   - Dereference NULL when trying to read registers
   - **Crash the entire emulator**

The fix adds a simple guard:
```asm
ldr     x9, [x8, #(TpidrEl0NativeContext)]
cbz     x9, 3f  /* If NULL, treat as host fault instead */
```

This ensures that even if a thread has the magic value, we only proceed with guest handling if there's actually a valid `GuestContext` to work with.

### Frida Signal Handler Conflict

**Problem:** Frida-gum also registers a SIGILL handler, which replaces NCE's handler. So when a hooked instruction executes:
1. `UDF #0` triggers SIGILL
2. Frida's handler catches it (not NCE's)
3. Frida doesn't know how to handle NCE hooks
4. Frida either aborts or chains to a wrong handler
5. **Crash or infinite loop**

**Solution:** Remove SIGILL from Frida's handled signals in `gumexceptor-posix.c`:
```c
const gint handled_signals[] = {
    SIGABRT,
    // SIGSEGV,  // Let NCE handle
    // SIGBUS,   // Let NCE handle
    // SIGILL,   // Let NCE handle - required for hooks!
    SIGFPE,
    SIGTRAP,
    SIGSYS,
};
```

---

## Debugging Guide

### Common Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| `Unmapped ReadBlock @ 0x...` | Invalid address | Check MOD0 base address, verify game version |
| `Failed to emulate instruction` | Unsupported PC-relative instruction | Add implementation to `interpreter_visitor.cpp` |
| `vtable bouncing` spam | Signal handler loop | Check instruction emulation returns `pc + 4` |
| Game crash after hook | Wrong flags or corrupted state | Add logging, verify flag calculation |
| Hook installed but no text | Wrong register/address | Check JS script for correct register |
| Instruction mismatch | Wrong game version or base | Verify MOD0 scan found correct base |

### Debug Logging

Add verbose logging in your Frida script:
```javascript
Interceptor.attach(nceTrampoline, {
    onEnter(args) {
        const pc = args[0];
        const context = args[1];
        const x0 = context.readPointer();
        const x1 = context.add(0x08).readPointer();
        const sp = context.add(0xF8).readPointer();
        
        console.log(`Hook triggered at PC=${pc}`);
        console.log(`  X0=${x0} X1=${x1} SP=${sp}`);
    }
});
```

### Verifying Hook Installation

Before installing, verify the instruction at the target address:
```javascript
const hookAddr = baseAddress.add(0x26D60);
const instruction = hookAddr.readU32();
console.log(`Address: ${hookAddr}, Instruction: ${instruction.toString(16)}`);

if (instruction === 0x710006FF) {
    console.log('✓ Instruction matches expected value');
    NceInstallExternalHook(uint64(hookAddr.toString()), instruction);
} else {
    console.log('✗ Instruction mismatch - check base address or game version');
}
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `scripts/libYuzu.js` | JavaScript hooking framework, MOD0 scanning, hook handlers, log callback registration |
| `scripts/NS_*.js` | Game-specific hook configurations |
| `src/core/arm/nce/nce_hooks.h` | External C API declarations for Frida, log levels enum |
| `src/core/arm/nce/nce_hooks.cpp` | Trampoline allocator, exported C functions, logging infrastructure |
| `src/core/arm/nce/interpreter_visitor.h` | `InterpreterVisitor` class, PC-relative instruction declarations |
| `src/core/arm/nce/interpreter_visitor.cpp` | PC-relative instruction emulation (ADR, branches, etc.) |
| `src/core/arm/nce/arm_nce.cpp` | Signal handler, legacy instruction emulation |
| `src/core/arm/nce/arm_nce.s` | Assembly signal handlers |
| `src/core/arm/nce/guest_context.h` | `GuestContext` structure definition |

---

## Future Improvements

1. **Hook Persistence:** Save/restore hooks across Frida restarts