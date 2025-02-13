import ctypes
import logging
import winreg

from ctypes import wintypes

logger = logging.getLogger(__name__)


# Define ULONG_PTR
if ctypes.sizeof(ctypes.c_void_p) == 8:  # 64-bit system
    ULONG_PTR = ctypes.c_uint64
else:  # 32-bit system
    ULONG_PTR = ctypes.c_uint32

# Relation types define the type of processor data returned (for core info, we use RelationProcessorCore)
RelationProcessorCore = 0  # Type used to identify cores
RelationAll = 0xffff
# Constants for GetSystemCpuSetInformation API
SystemLogicalProcessorInformation = 0  # Not used here, kept for reference
SystemCpuSetInformation = 1

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

class SYSTEM_CPU_SET_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.DWORD),  # Size of the structure
        ("Type", wintypes.DWORD),  # Should be 1 (SystemCpuSetInformation)
        ("Id", wintypes.DWORD),  # Logical CPU ID
        ("Group", wintypes.WORD),  # CPU group number
        ("LogicalProcessorIndex", wintypes.BYTE),
        ("CoreIndex", wintypes.BYTE),
        ("LastLevelCacheIndex", wintypes.BYTE),
        ("NumaNodeIndex", wintypes.BYTE),
        ("EfficiencyClass", wintypes.BYTE),  # Efficiency class field
        ("AllFlags", wintypes.BYTE),
        ("SchedulingClass", wintypes.BYTE),  # Scheduling class field
        ("Reserved", wintypes.BYTE * 9),
        ("Reserved2", wintypes.DWORD),
        ("GroupAffinity", GROUP_AFFINITY),
    ]

# SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX base container
class SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX(ctypes.Structure):
    _fields_ = [
        ("Relationship", wintypes.DWORD),  # RelationProcessorCore, RelationCache, etc.
        ("Size", wintypes.DWORD),  # Size of the structure
        ("Processor", PROCESSOR_RELATIONSHIP),  # Embedded processor structure
    ]


def get_processor_info() -> list[tuple[int, int, int]]:
    ret: list[tuple[int, int, int]] = []

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

    # Call GetSystemCpuSetInformation to retrieve scheduling data
    cpu_set_info = get_cpu_set_information()  # Use existing helper to fetch CPU set info
    scheduling_class_map = {cpu["Logical Processor Index"]: cpu["Scheduling Class"] for cpu in cpu_set_info}

    # Parse the buffer
    offset = 0
    while offset < required_size.value:
        info = ctypes.cast(ctypes.byref(buffer, offset),
                           ctypes.POINTER(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)).contents
        if info.Relationship == RelationProcessorCore:
            efficiency_class = info.Processor.EfficiencyClass
            mask = info.Processor.GroupMask[0].Mask
            # Map each bit in the affinity mask to logical processors
            for i in range(mask.bit_length()):  # Iterate over each bit in the mask (logical processors)
                if mask & (1 << i):  # Check if logical processor `i` is part of this physical core group
                    scheduling_class = scheduling_class_map.get(i, 0)  # Get Scheduling Class (default to 0)
                    # Only associate this logical processor (physical core + index connection)
                    ret.append((efficiency_class, scheduling_class, mask))
                    break  # Use only the first logical processor for this physical core

        # Move to the next structure in the buffer
        offset += info.Size

    return ret


def get_cpu_set_information():
    """
    Queries logical CPU information using GetSystemCpuSetInformation and retrieves relevant fields.

    Returns:
        list[dict]: A list of dictionaries containing information about each logical CPU.
    """
    kernel32 = ctypes.WinDLL("Kernel32.dll")

    kernel32.GetSystemCpuSetInformation.argtypes = [
        ctypes.c_void_p,  # PSYSTEM_CPU_SET_INFORMATION (pointer, can be None)
        wintypes.ULONG,  # ULONG BufferLength (size of the buffer, 0 at first call)
        ctypes.POINTER(wintypes.ULONG),  # PULONG ReturnedLength (pointer to get size)
        wintypes.HANDLE,  # HANDLE Process (handle to the process, NULL for current)
        wintypes.ULONG  # ULONG Flags (reserved, must be 0)
    ]
    kernel32.GetSystemCpuSetInformation.restype = wintypes.BOOL

    # Determine required buffer size
    buffer_size = wintypes.DWORD(0)
    result = kernel32.GetSystemCpuSetInformation(
        None,  # No buffer since we're querying the size
        0,  # BufferLength = 0
        ctypes.byref(buffer_size),  # Retrieve required buffer size
        None,  # Current process
        0  # Reserved
    )
    if buffer_size.value == 0:
        raise RuntimeError("GetSystemCpuSetInformation failed to get buffer size")

    # Allocate buffer
    buffer = ctypes.create_string_buffer(buffer_size.value)

    # Retrieve CPU set information
    result = kernel32.GetSystemCpuSetInformation(
        ctypes.byref(buffer),  # Provide allocated buffer
        buffer_size,  # Size of the buffer
        ctypes.byref(buffer_size),  # Size of actual data written
        None,  # Current process
        0  # Reserved
    )

    if result == 0:
        raise RuntimeError("GetSystemCpuSetInformation failed to retrieve information")

    # Parse buffer
    cpu_info_list = []
    offset = 0
    while offset < buffer_size.value:
        # Cast buffer to SYSTEM_CPU_SET_INFORMATION structure
        cpu_info = ctypes.cast(
            ctypes.byref(buffer, offset),
            ctypes.POINTER(SYSTEM_CPU_SET_INFORMATION)
        ).contents

        # Add relevant fields to the dictionary
        cpu_info_list.append({
            "CPU Id": cpu_info.Id,
            "Logical Processor Index": cpu_info.LogicalProcessorIndex,
            "Core Index": cpu_info.CoreIndex,
            "Efficiency Class": cpu_info.EfficiencyClass,
            "Scheduling Class": cpu_info.SchedulingClass
        })

        # Move to the next structure
        offset += cpu_info.Size

    return cpu_info_list


def get_p_core_affinity() -> int:
    """
    Calculate the affinity mask for all logical processors associated with performance cores (P-cores).

    Returns:
        int: An affinity mask where each bit represents a logical processor, with `1` for P-cores.
    """
    processor_info = get_processor_info()  # Retrieve individual core data
    p_core_affinity_mask = 0  # Initialize to zero

    for efficiency_class, _, mask in processor_info:
        if efficiency_class > 0:  # Check if this is a P-core
            p_core_affinity_mask |= mask  # Add this core's bitmask to the affinity mask

    return p_core_affinity_mask


def get_e_core_affinity() -> int:
    """
    Calculate the affinity mask for all logical processors associated with efficiency cores (E-cores).

    Returns:
        int: An affinity mask where each bit represents a logical processor, with `1` for P-cores.
    """
    processor_info = get_processor_info()  # Retrieve individual core data
    e_core_affinity_mask = 0  # Initialize to zero

    for efficiency_class, _, mask in processor_info:
        if efficiency_class == 0:  # Check if this is an E-core
            e_core_affinity_mask |= mask  # Add this core's bitmask to the affinity mask

    return e_core_affinity_mask


def get_cpus_from_affinity(affinity_mask: int) -> list[int]:
    core_ids = []
    bit_position = 0

    while affinity_mask:  # While there are still bits set in the mask
        if affinity_mask & 1:  # Check if the least significant bit is set
            core_ids.append(bit_position)
        affinity_mask >>= 1  # Shift the mask to the right to examine the next bit
        bit_position += 1

    return core_ids


def determine_performance_class(efficiency_class: int, flags: int) -> int:
    """
    Determines the performance class of a processor core.

    Args:
        efficiency_class (int): The processor core's efficiency class as given
                                by GetLogicalProcessorInformationEx.
        flags (int): Processor flags or the group affinity mask.

    Returns:
        int: The core's performance class.
    """
    # Example: Map efficiency class & flags to performance class
    # You may override or extend this logic depending on architecture
    if efficiency_class == 0:
        return 0  # Efficiency cores (E-cores)
    elif efficiency_class == 1:
        return 1  # Performance cores (P-cores)
    else:
        # AMD or Custom: Infer based on flags or other attributes
        return (flags >> 2) & 0b11  # Example: Mock performance tier from 'flags'


def get_performance_classes() -> dict:
    """
    Groups logical cores by their performance class.

    Returns:
        dict: A dictionary mapping each performance class to its associated logical cores.
              Keys are `performance_class` integers, and values are combined bitmasks of logical processors.
    """
    processor_info = get_processor_info()  # Retrieve individual core data
    performance_classes = {}  # Dictionary to store logical processors mapped by performance class

    for _, performance_class, mask in processor_info:
        # Group logical processors by performance class
        if performance_class not in performance_classes:
            performance_classes[performance_class] = 0
        performance_classes[performance_class] |= mask  # Aggregate logical processors for the class using bitwise OR

    return performance_classes


def get_cpu_name():
    # Open the registry key for the CPU information
    registry_key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    )

    # Read the value of ProcessorNameString
    cpu_name, _ = winreg.QueryValueEx(registry_key, "ProcessorNameString")

    # Close the registry key
    winreg.CloseKey(registry_key)

    return cpu_name.strip()


def log_cpu():
    logger.debug(f"CPU: {get_cpu_name()}")
    processor_info = get_processor_info()
    efficiency_classes = sorted({entry[0] for entry in processor_info})
    if len(efficiency_classes) > 1:
        logger.info(f"CPU cores have different efficiency classes: [{efficiency_classes[0]}-{efficiency_classes[-1]}]")
        for efficiency_class in sorted(efficiency_classes, reverse=True):
            for eclass, pclass, mask in processor_info:
                if eclass != efficiency_class:
                    continue
                logger.debug(f"logical cores with efficiency class {efficiency_class}: {get_cpus_from_affinity(mask)}")
    else:
        logger.debug(f"all CPU cores have the same efficiency class {efficiency_classes[0]}")
    performance_classes = sorted({entry[1] for entry in processor_info})
    if len(performance_classes) > 1:
        logger.info(f"CPU cores have different efficiency classes: [{performance_classes[0]}-{performance_classes[-1]}]")
        for performance_class in sorted(performance_classes, reverse=True):
            for eclass, pclass, mask in processor_info:
                if pclass != performance_class:
                    continue
                logger.debug(f"logical cores with performance class {performance_class}: {get_cpus_from_affinity(mask)}")
    else:
        logger.debug(f"all CPU cores have the same performance class {performance_classes[0]}")


if __name__ == '__main__':
    print(get_cpu_name())
    print(get_processor_info())
    p_core_affinity_mask = get_p_core_affinity()
    print(f"Performance Cores: {get_cpus_from_affinity(p_core_affinity_mask)}")
    e_core_affinity_mask = get_e_core_affinity()
    print(f"Efficiency Cores: {get_cpus_from_affinity(e_core_affinity_mask)}")
    performance_classes = get_performance_classes()
    for plcass, affinity_mask in performance_classes.items():
        print(f"Performance Class {plcass}: {get_cpus_from_affinity(affinity_mask)}")
