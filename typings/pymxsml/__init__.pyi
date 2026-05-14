MXSML_TEMPERATURE_HOTSPOT: int

class _DeviceInfo:
    deviceName: str
    bdfId: str
    uuid: str

class _MemoryInfo:
    vramUse: int
    vramTotal: int

class _BoardPowerInfo:
    power: float

def mxSmlInit() -> object: ...
def mxSmlGetBoardPowerInfo(index: int) -> list[_BoardPowerInfo]: ...
def mxSmlGetDeviceCount() -> int: ...
def mxSmlGetDeviceInfo(index: int) -> _DeviceInfo: ...
def mxSmlGetMemoryInfo(index: int) -> _MemoryInfo: ...
def mxSmlGetTemperatureInfo(index: int, sensor: int) -> float: ...
