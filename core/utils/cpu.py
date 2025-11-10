import ctypes
import logging
import traceback
import winreg

from ctypes import wintypes
from io import BytesIO
from matplotlib import pyplot as plt, patches

logger = logging.getLogger(__name__)

__all__ = [
    "get_cpu_name",
    "get_processor_info",
    "get_cpu_set_information",
    "get_scheduling_classes",
    "get_cache_info",
    "get_e_core_affinity",
    "get_p_core_affinity",
    "get_cpus_from_affinity",
    "create_cpu_topology_visualization"
]

# Define ULONG_PTR
if ctypes.sizeof(ctypes.c_void_p) == 8:  # 64-bit system
    ULONG_PTR = ctypes.c_uint64
else:  # 32-bit system
    ULONG_PTR = ctypes.c_uint32

# Relation types define the type of processor data returned (for core info, we use RelationProcessorCore)
RelationProcessorCore = 0
RelationNumaNode = 1
RelationCache = 2
RelationProcessorPackage = 3
RelationGroup = 4
RelationAll = 0xffff
# Cache type constants
CacheUnified = 0
CacheInstruction = 1
CacheData = 2
CacheTrace = 3
# Constants for GetSystemCpuSetInformation API
SystemLogicalProcessorInformation = 0  # Not used here, kept for reference
SystemCpuSetInformation = 1

PROCESSOR_CACHE_TYPE = ctypes.c_int
MAXIMUM_PROC_PER_GROUP = 64  # Windows supports up to 64 processors per group


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


class CACHE_RELATIONSHIP(ctypes.Structure):
    _fields_ = [
        ("Level", ctypes.c_ubyte),
        ("Associativity", ctypes.c_ubyte),
        ("LineSize", ctypes.c_ushort),
        ("CacheSize", ctypes.c_ulong),
        ("Type", PROCESSOR_CACHE_TYPE),
        ("Reserved", ctypes.c_ubyte * 18),  # Adjusted padding
        ("GroupMask", GROUP_AFFINITY)
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


class SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [
            ("Processor", PROCESSOR_RELATIONSHIP),
            ("Cache", CACHE_RELATIONSHIP),
        ]

    _anonymous_ = ("u",)
    _fields_ = [
        ("Relationship", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("u", _U)
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
    kernel32.GetSystemCpuSetInformation(
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
    If only one efficiency class (0) exists, these are considered P-cores.

    Returns:
        int: An affinity mask where each bit represents a logical processor, with `1` for P-cores.
    """
    processor_info = get_processor_info()  # Retrieve individual core data
    p_core_affinity_mask = 0  # Initialize to zero

    # Check if we have multiple efficiency classes
    efficiency_classes = {ec for ec, _, _ in processor_info}

    if len(efficiency_classes) == 1:
        # If only one class exists (0), treat these as P-cores
        for _, _, mask in processor_info:
            p_core_affinity_mask |= mask
    else:
        # Multiple classes exist, use cores with efficiency_class > 0
        for efficiency_class, _, mask in processor_info:
            if efficiency_class > 0:
                p_core_affinity_mask |= mask

    return p_core_affinity_mask


def get_e_core_affinity() -> int:
    """
    Calculate the affinity mask for all logical processors associated with efficiency cores (E-cores).
    If only one efficiency class (0) exists, there are no E-cores.

    Returns:
        int: An affinity mask where each bit represents a logical processor, with `1` for E-cores.
    """
    processor_info = get_processor_info()  # Retrieve individual core data
    e_core_affinity_mask = 0  # Initialize to zero

    # Check if we have multiple efficiency classes
    efficiency_classes = {ec for ec, _, _ in processor_info}

    if len(efficiency_classes) > 1:
        # Only consider efficiency class 0 as E-cores if multiple classes exist
        for efficiency_class, _, mask in processor_info:
            if efficiency_class == 0:
                e_core_affinity_mask |= mask

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


def determine_scheduling_class(efficiency_class: int, flags: int) -> int:
    """
    Determines the scheduling class of a processor core.

    Args:
        efficiency_class (int): The processor core's efficiency class as given
                                by GetLogicalProcessorInformationEx.
        flags (int): Processor flags or the group affinity mask.

    Returns:
        int: The core's scheduling class.
    """
    # Example: Map efficiency class & flags to scheduling class
    # You may override or extend this logic depending on architecture
    if efficiency_class == 0:
        return 0  # Efficiency cores (E-cores)
    elif efficiency_class == 1:
        return 1  # Performance cores (P-cores)
    else:
        # AMD or Custom: Infer based on flags or other attributes
        return (flags >> 2) & 0b11  # Example: Mock performance tier from 'flags'


def get_scheduling_classes() -> dict:
    """
    Groups logical cores by their scheduling class.

    Returns:
        dict: A dictionary mapping each scheduling class to its associated logical cores.
              Keys are `scheduling_class` integers, and values are combined bitmasks of logical processors.
    """
    processor_info = get_processor_info()  # Retrieve individual core data
    scheduling_classes = {}  # Dictionary to store logical processors mapped by scheduling class

    for _, scheduling_class, mask in processor_info:
        # Group logical processors by scheduling class
        if scheduling_class not in scheduling_classes:
            scheduling_classes[scheduling_class] = 0
        scheduling_classes[scheduling_class] |= mask  # Aggregate logical processors for the class using bitwise OR

    return scheduling_classes


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


def get_cache_info():
    kernel32 = ctypes.WinDLL("Kernel32.dll")

    # First call to get required buffer size
    buffer_size = wintypes.DWORD(0)
    if not kernel32.GetLogicalProcessorInformationEx(RelationCache, None, ctypes.byref(buffer_size)):
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(ctypes.get_last_error())

    # Create the buffer
    buffer = ctypes.create_string_buffer(buffer_size.value)
    # Retrieve the information
    result = kernel32.GetLogicalProcessorInformationEx(RelationCache, buffer, ctypes.byref(buffer_size))

    if result == 0:
        raise ctypes.WinError()

    offset = 0
    cache_info = []
    while offset < buffer_size.value - ctypes.sizeof(wintypes.DWORD) * 2:  # Space for Relationship and Size fields
        info = ctypes.cast(buffer[offset:], ctypes.POINTER(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)).contents

        if info.Size == 0:  # Invalid entry, break the loop
            break

        if info.Relationship == RelationCache:
            cores = get_cpus_from_affinity(info.Cache.GroupMask.Mask)
            entry = {
                'level': info.Cache.Level,
                'type': int(info.Cache.Type),
                'size': info.Cache.CacheSize,
                'line_size': info.Cache.LineSize,
                'cores': sorted(cores)
            }
            cache_info.append(entry)

        offset += info.Size

        # Break if we don't have enough bytes left for another complete entry
        if offset >= buffer_size.value:
            break

    return sorted(cache_info, key=lambda x: x['level'])


def create_cpu_topology_visualization(p_cores, e_cores, cache_structure, display: bool = False):
    if not display:
        plt.switch_backend('agg')
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(20, 12))
    ax.set_aspect('equal')

    # Lighter colors for dark theme
    p_core_color = '#2E6B9B'  # Lighter blue
    e_core_color = '#2B7A44'  # Lighter green
    l1_color = '#9B2E4F'  # Lighter red
    l2_color = '#6B4488'  # Lighter purple
    l3_color = '#8C8544'  # Lighter gold
    text_color = '#E0E0E0'  # Light gray for text

    core_width = 0.8
    core_height = 0.8
    core_gap = 0.4
    x_spacing = core_width + core_gap
    y_spacing = 1.2
    l3_height = 0.8
    l3_spacing = 0

    # Calculate layout dimensions
    p_cores_per_row = len(p_cores) // 2
    e_cores_per_row = len(e_cores) // 2
    p_rows = (len(p_cores) + p_cores_per_row - 1) // p_cores_per_row
    e_rows = (len(e_cores) + e_cores_per_row - 1) // e_cores_per_row if e_cores else 0

    # Calculate total width needed
    p_cores_width = p_cores_per_row * core_width + (p_cores_per_row - 1) * core_gap
    e_cores_width = e_cores_per_row * core_width + (e_cores_per_row - 1) * core_gap
    e_section_start = p_cores_width + x_spacing
    total_width = p_cores_width + ((e_cores_width + x_spacing) if e_cores else 0)

    # First, determine which rows have L3 caches
    l3_caches = [cache for cache in cache_structure if cache['level'] == 3]
    rows_with_l3 = set()
    for l3_cache in l3_caches:
        shared_cores = sorted(l3_cache['cores'])
        row = min(shared_cores) // p_cores_per_row
        rows_with_l3.add(row)


    def format_size(size):
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.0f}M"
        elif size >= 1024:
            return f"{size / 1024:.0f}K"
        return f"{size}B"

    # Group cores by their L2 cache sharing
    l2_groups = {}
    for cache in cache_structure:
        if cache['level'] == 2:
            key = tuple(sorted(cache['cores']))
            l2_groups[key] = {'cores': cache['cores'], 'size': cache['size']}

    # Draw P-cores
    for i, core in enumerate(sorted(p_cores)):
        row = i // p_cores_per_row
        x = (i % p_cores_per_row) * x_spacing

        # Calculate y position based on whether previous rows had L3 caches
        y = 0
        for prev_row in range(row):
            if prev_row in rows_with_l3:
                y += y_spacing * 3 + l3_spacing
            else:
                y += y_spacing * 3
        rect = patches.Rectangle((x, y), core_width, core_height, facecolor=p_core_color, edgecolor='white',
                                 linewidth=0.5)
        ax.add_patch(rect)
        ax.text(x + core_width / 2, y + core_height / 2, f"P{core}", ha='center', va='center', color=text_color)

        if i % 2 == 0:
            for cache in cache_structure:
                if cache['level'] == 1 and core in cache['cores']:
                    if cache['type'] == 2:  # L1i
                        l1i = patches.Rectangle((x, y - 0.6), x_spacing * 2 - 0.4, 0.4,
                                                facecolor=l1_color, edgecolor='white', linewidth=0.5)
                        ax.add_patch(l1i)
                        ax.text(x + x_spacing - 0.2, y - 0.4, f"L1-I {format_size(cache['size'])}",
                                ha='center', va='center', fontsize=8, color=text_color)
                    elif cache['type'] == 1:  # L1d
                        l1d = patches.Rectangle((x, y - 1.0), x_spacing * 2 - 0.4, 0.4,
                                                facecolor=l1_color, edgecolor='white', linewidth=0.5)
                        ax.add_patch(l1d)
                        ax.text(x + x_spacing - 0.2, y - 0.8, f"L1-D {format_size(cache['size'])}",
                                ha='center', va='center', fontsize=8, color=text_color)

            for group in l2_groups.values():
                if core in group['cores']:
                    l2 = patches.Rectangle((x, y - 1.4), x_spacing * 2 - 0.4, 0.4,
                                           facecolor=l2_color, edgecolor='white', linewidth=0.5)
                    ax.add_patch(l2)
                    ax.text(x + x_spacing - 0.2, y - 1.2, f"L2 {format_size(group['size'])}",
                            ha='center', va='center', fontsize=8, color=text_color)
                    break

    # Draw E-cores
    for i, core in enumerate(sorted(e_cores)):
        row = i // e_cores_per_row
        x = (i % e_cores_per_row) * x_spacing + e_section_start

        # Calculate y position based on whether previous rows had L3 caches
        y = 0
        for prev_row in range(row):
            if prev_row in rows_with_l3:
                y += y_spacing * 3 + l3_spacing
            else:
                y += y_spacing * 3
        rect = patches.Rectangle((x, y), core_width, core_height,
                                 facecolor=e_core_color, edgecolor='white', linewidth=0.5)
        ax.add_patch(rect)
        ax.text(x + core_width / 2, y + core_height / 2, f"E{core}", ha='center', va='center', color=text_color)

        for cache in cache_structure:
            if cache['level'] == 1 and core in cache['cores']:
                if cache['type'] == 2:  # L1i
                    l1i = patches.Rectangle((x, y - 0.6), core_width, 0.4,
                                            facecolor=l1_color, edgecolor='white', linewidth=0.5)
                    ax.add_patch(l1i)
                    ax.text(x + core_width / 2, y - 0.4, f"L1-I {format_size(cache['size'])}",
                            ha='center', va='center', fontsize=8, color=text_color)
                elif cache['type'] == 1:  # L1d
                    l1d = patches.Rectangle((x, y - 1.0), core_width, 0.4,
                                            facecolor=l1_color, edgecolor='white', linewidth=0.5)
                    ax.add_patch(l1d)
                    ax.text(x + core_width / 2, y - 0.8, f"L1-D {format_size(cache['size'])}",
                            ha='center', va='center', fontsize=8, color=text_color)

        if i % 4 == 0:
            for group in l2_groups.values():
                if core in group['cores']:
                    l2_width = x_spacing * 4 - 0.4
                    l2 = patches.Rectangle((x, y - 1.4), l2_width, 0.4,
                                           facecolor=l2_color, edgecolor='white', linewidth=0.5)
                    ax.add_patch(l2)
                    ax.text(x + l2_width / 2, y - 1.2, f"L2 {format_size(group['size'])}",
                            ha='center', va='center', fontsize=8, color=text_color)
                    break

    # Draw L3 cache
    for l3_cache in l3_caches:
        shared_cores = sorted(l3_cache['cores'])
        leftmost_core = min(shared_cores)
        rightmost_core = max(shared_cores)
        row = leftmost_core // p_cores_per_row

        # Calculate y position for L3 cache - simplified
        y_position = row * y_spacing * 3 - 2.4  # Changed from -2.0 to -2.4 to avoid overlap with L1/L2

        # Calculate the x-coordinates for this L3 section
        if leftmost_core in p_cores:
            start_x = (leftmost_core % p_cores_per_row) * x_spacing
        else:
            # For E-cores, adjust the starting position (only if e_cores exists)
            if e_cores:
                e_core_index = sorted(e_cores).index(leftmost_core)
                start_x = e_section_start + (e_core_index % e_cores_per_row) * x_spacing
            else:
                continue

        if rightmost_core in p_cores:
            end_x = (rightmost_core % p_cores_per_row) * x_spacing
        else:
            # For E-cores, adjust the ending position (only if e_cores exists)
            if e_cores:
                e_core_index = sorted(e_cores).index(rightmost_core)
                end_x = e_section_start + (e_core_index % e_cores_per_row) * x_spacing
            else:
                continue

        l3_width = end_x - start_x + core_width

        # Draw this L3 section at the correct y-position
        l3 = patches.Rectangle((start_x, y_position), l3_width, l3_height,
                               facecolor=l3_color, edgecolor='white', linewidth=0.5)
        ax.add_patch(l3)
        ax.text(start_x + l3_width / 2, y_position + l3_height / 2,
                f"L3 {format_size(l3_cache['size'])} (Cores {min(shared_cores)}-{max(shared_cores)})",
                ha='center', va='center', color=text_color)

    # Create legend elements
    legend_elements = [
        patches.Patch(facecolor=p_core_color, edgecolor='white', label='Performance Cores'),
        patches.Patch(facecolor=e_core_color, edgecolor='white', label='Efficiency Cores'),
        patches.Patch(facecolor=l1_color, edgecolor='white', label='L1 Cache (Data & Instruction)'),
        patches.Patch(facecolor=l2_color, edgecolor='white', label='L2 Cache'),
        patches.Patch(facecolor=l3_color, edgecolor='white', label='L3 Cache')
    ]

    # Add legend in the bottom right corner
    ax.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(0.98, 0.98),
              ncol=1, fancybox=True, shadow=True)

    # Set plot limits and remove axes
    margin = 1
    ax.set_xlim(-margin, total_width + margin)
    ax.set_ylim(-4, max(p_rows, e_rows) * y_spacing * 3 + margin)
    ax.axis('off')

    plt.title(f"CPU Topology with Cache Hierarchy for {get_cpu_name()}", color=text_color, y=0.98)

    # Set figure background to dark
    fig.patch.set_facecolor('#1C1C1C')
    ax.set_facecolor('#1C1C1C')

    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format='png', facecolor='#1C1C1C')
    if display:
        plt.show()

    plt.close(fig)  # Close the figure to free memory
    buf.seek(0)
    return buf


if __name__ == '__main__':
    try:
        print(get_cpu_name())
        print(get_processor_info())
        p_core_affinity_mask = get_p_core_affinity()
        print(f"Performance Cores: {get_cpus_from_affinity(p_core_affinity_mask)}")
        e_core_affinity_mask = get_e_core_affinity()
        print(f"Efficiency Cores: {get_cpus_from_affinity(e_core_affinity_mask)}")
        scheduling_classes = get_scheduling_classes()
        for plcass, affinity_mask in scheduling_classes.items():
            print(f"Scheduling Class {plcass}: {get_cpus_from_affinity(affinity_mask)}")
        print("\nCache Information:")
        cache_info = get_cache_info()
        try:
            for cache in cache_info:
                cache_type = ['Unified', 'Instruction', 'Data', 'Trace'][cache['type']]
                print(f"L{cache['level']} {cache_type} Cache:")
                print(f"  Size: {cache['size']/1024:.0f}KB")
                print(f"  Line Size: {cache['line_size']} bytes")
                print(f"  Shared by cores: {cache['cores']}")
        except Exception:
            pass
        create_cpu_topology_visualization(get_cpus_from_affinity(p_core_affinity_mask),
                                          get_cpus_from_affinity(e_core_affinity_mask),
                                          cache_info, True)
    except Exception as e:
        traceback.print_exc()
