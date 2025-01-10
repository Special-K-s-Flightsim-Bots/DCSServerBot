import ctypes
from ctypes import wintypes

# Define ULONG_PTR
if ctypes.sizeof(ctypes.c_void_p) == 8:  # 64-bit system
    ULONG_PTR = ctypes.c_uint64
else:  # 32-bit system
    ULONG_PTR = ctypes.c_uint32

# Relation types define the type of processor data returned (for core info, we use RelationProcessorCore)
RelationProcessorCore = 0  # Type used to identify cores


# Define GROUP_AFFINITY structure
class GROUP_AFFINITY(ctypes.Structure):
    _fields_ = [
        ("Mask", ULONG_PTR),  # Bitmap for logical processors
        ("Group", wintypes.WORD),
        ("Reserved", wintypes.WORD * 3),
    ]


# Define PROCESSOR_RELATIONSHIP structure
class PROCESSOR_RELATIONSHIP(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_byte),  # Flags to identify the type (P-core, E-core, SMT, etc.)
        ("EfficiencyClass", ctypes.c_byte),  # Efficiency class (higher = P-core, lower = E-core)
        ("Reserved", ctypes.c_byte * 20),
        ("GroupCount", wintypes.WORD),  # Number of groups
        ("GroupMask", GROUP_AFFINITY * 1),  # Array of group masks
    ]


# SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX base container
class SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX(ctypes.Structure):
    _fields_ = [
        ("Relationship", wintypes.DWORD),  # RelationProcessorCore, RelationCache, etc.
        ("Size", wintypes.DWORD),  # Size of the structure
        ("Processor", PROCESSOR_RELATIONSHIP),  # Embedded processor structure
    ]


def get_processor_info() -> list[tuple[int, int]]:
    ret: list[tuple[int, int]] = []

    # Load the kernel32.dll library
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Specify the `GetLogicalProcessorInformationEx` function
    logical_proc_info_ex = kernel32.GetLogicalProcessorInformationEx
    logical_proc_info_ex.restype = wintypes.BOOL
    logical_proc_info_ex.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]

    # Determine the size of the buffer
    required_size = wintypes.DWORD(0)
    if not logical_proc_info_ex(RelationProcessorCore, None, ctypes.byref(required_size)):
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(ctypes.get_last_error())

    # Allocate the buffer
    buffer = ctypes.create_string_buffer(required_size.value)

    # Call the function to populate the buffer
    if not logical_proc_info_ex(RelationProcessorCore, buffer, ctypes.byref(required_size)):
        raise ctypes.WinError(ctypes.get_last_error())

    # Parse the buffer
    offset = 0
    while offset < required_size.value:
        info = ctypes.cast(ctypes.byref(buffer, offset),
                           ctypes.POINTER(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)).contents
        if info.Relationship == RelationProcessorCore:
            flags = info.Processor.Flags
            efficiency_class = info.Processor.EfficiencyClass
            ret.append((efficiency_class, flags))

        # Move to the next structure in the buffer
        offset += info.Size

    return ret


def get_p_core_affinity():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    logical_proc_info_ex = kernel32.GetLogicalProcessorInformationEx
    logical_proc_info_ex.restype = wintypes.BOOL
    logical_proc_info_ex.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]

    # Step 1: Retrieve the buffer size required
    required_size = wintypes.DWORD(0)
    if not logical_proc_info_ex(RelationProcessorCore, None, ctypes.byref(required_size)):
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(ctypes.get_last_error())

    # Step 2: Allocate the buffer
    buffer = ctypes.create_string_buffer(required_size.value)

    # Step 3: Populate the buffer with processor information
    if not logical_proc_info_ex(RelationProcessorCore, buffer, ctypes.byref(required_size)):
        raise ctypes.WinError(ctypes.get_last_error())

    # Step 4: Parse the buffer to find all P-cores
    affinity_mask = 0
    offset = 0
    while offset < required_size.value:
        info = ctypes.cast(
            ctypes.byref(buffer, offset),
            ctypes.POINTER(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)
        ).contents

        if info.Relationship == RelationProcessorCore:  # Only handle processor core relationships
            efficiency_class = info.Processor.EfficiencyClass
            flags = info.Processor.Flags
            mask = info.Processor.GroupMask[0].Mask  # Logical processors bitmask

            # Check if it's a P-core (EfficiencyClass > 0)
            if efficiency_class > 0:  # Adjust to match your P-core/E-core criteria
                affinity_mask |= mask  # Add this core's logical processors to the affinity mask

        # Move to the next entry in the buffer
        offset += info.Size

    return affinity_mask


def get_cpus_from_affinity(affinity_mask: int) -> list[int]:
    core_ids = []
    bit_position = 0

    while affinity_mask:  # While there are still bits set in the mask
        if affinity_mask & 1:  # Check if the least significant bit is set
            core_ids.append(bit_position)
        affinity_mask >>= 1  # Shift the mask to the right to examine the next bit
        bit_position += 1

    return core_ids


if __name__ == '__main__':
    print(get_processor_info())
    print(get_p_core_affinity())
    print(get_cpus_from_affinity(get_p_core_affinity()))
