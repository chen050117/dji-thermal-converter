# encoding: utf-8
"""
DJI Thermal SDK 参数读取脚本
从 R-JPEG 热成像图像中读取发射率、环境温度、反射温度、空气湿度等参数

使用方法:
    python get_thermal_params.py <图像路径>
    
示例:
    python get_thermal_params.py input_dir/DJI_20251126161006_0001_T.JPG
"""

import os
import sys
import ctypes
from ctypes import c_int32, c_uint8, c_uint32, c_float, c_void_p, POINTER, Structure, byref

# SDK DLL 路径 (相对于脚本位置)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SDK_DLL_DIR = os.path.join(SCRIPT_DIR, "dji_thermal_sdk_v1.8_20250829", "tsdk-core", "lib", "windows", "release_x64")


# ==================== 结构体定义 ====================
class DirpMeasurementParams(Structure):
    """测量参数结构体"""
    _fields_ = [
        ("distance", c_float),      # 目标距离 (米)
        ("humidity", c_float),      # 相对湿度 (%)
        ("emissivity", c_float),    # 发射率
        ("reflection", c_float),    # 反射温度 (摄氏度)
        ("ambient_temp", c_float),  # 环境温度 (摄氏度)
    ]


class DirpResolution(Structure):
    """图像分辨率结构体"""
    _fields_ = [
        ("width", c_int32),
        ("height", c_int32),
    ]


class DirpRjpegVersion(Structure):
    """R-JPEG 版本结构体"""
    _fields_ = [
        ("rjpeg", c_uint32),
        ("header", c_uint32),
        ("curve", c_uint32),
    ]


class DirpParamRange(Structure):
    """参数范围"""
    _fields_ = [
        ("min", c_float),
        ("max", c_float),
    ]


class DirpMeasurementParamsRange(Structure):
    """测量参数范围结构体"""
    _fields_ = [
        ("distance", DirpParamRange),
        ("humidity", DirpParamRange),
        ("emissivity", DirpParamRange),
        ("reflection", DirpParamRange),
        ("ambient_temp", DirpParamRange),
    ]


# ==================== SDK 加载 ====================
def load_sdk():
    """加载 DJI Thermal SDK DLL"""
    # 添加 DLL 搜索路径
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(SDK_DLL_DIR)
    
    # 设置环境变量
    os.environ['PATH'] = SDK_DLL_DIR + os.pathsep + os.environ.get('PATH', '')
    
    dll_path = os.path.join(SDK_DLL_DIR, "libdirp.dll")
    if not os.path.exists(dll_path):
        raise FileNotFoundError(f"SDK DLL 未找到: {dll_path}")
    
    # 加载 DLL
    sdk = ctypes.CDLL(dll_path)
    
    # 定义函数签名
    sdk.dirp_create_from_rjpeg.argtypes = [POINTER(c_uint8), c_int32, POINTER(c_void_p)]
    sdk.dirp_create_from_rjpeg.restype = c_int32
    
    sdk.dirp_destroy.argtypes = [c_void_p]
    sdk.dirp_destroy.restype = c_int32
    
    sdk.dirp_get_measurement_params.argtypes = [c_void_p, POINTER(DirpMeasurementParams)]
    sdk.dirp_get_measurement_params.restype = c_int32
    
    sdk.dirp_get_measurement_params_range.argtypes = [c_void_p, POINTER(DirpMeasurementParamsRange)]
    sdk.dirp_get_measurement_params_range.restype = c_int32
    
    sdk.dirp_get_rjpeg_resolution.argtypes = [c_void_p, POINTER(DirpResolution)]
    sdk.dirp_get_rjpeg_resolution.restype = c_int32
    
    sdk.dirp_get_rjpeg_version.argtypes = [c_void_p, POINTER(DirpRjpegVersion)]
    sdk.dirp_get_rjpeg_version.restype = c_int32
    
    return sdk


# ==================== 参数读取 ====================
def get_thermal_params(image_path: str) -> dict:
    """
    从热成像图像中读取参数
    
    Args:
        image_path: R-JPEG 图像路径
        
    Returns:
        包含热成像参数的字典
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图像文件未找到: {image_path}")
    
    # 加载 SDK
    sdk = load_sdk()
    
    # 读取图像数据
    with open(image_path, 'rb') as f:
        rjpeg_data = f.read()
    
    rjpeg_size = len(rjpeg_data)
    rjpeg_buffer = (c_uint8 * rjpeg_size).from_buffer_copy(rjpeg_data)
    
    # 创建句柄
    handle = c_void_p()
    ret = sdk.dirp_create_from_rjpeg(rjpeg_buffer, rjpeg_size, byref(handle))
    if ret != 0:
        raise RuntimeError(f"创建 DIRP 句柄失败, 错误码: {ret}")
    
    try:
        result = {
            "file_path": os.path.abspath(image_path),
            "file_name": os.path.basename(image_path),
        }
        
        # 获取分辨率
        resolution = DirpResolution()
        ret = sdk.dirp_get_rjpeg_resolution(handle, byref(resolution))
        if ret == 0:
            result["resolution"] = {
                "width": resolution.width,
                "height": resolution.height,
            }
        
        # 获取版本信息
        version = DirpRjpegVersion()
        ret = sdk.dirp_get_rjpeg_version(handle, byref(version))
        if ret == 0:
            result["rjpeg_version"] = {
                "rjpeg": hex(version.rjpeg),
                "header": hex(version.header),
                "curve": hex(version.curve),
            }
        
        # 获取测量参数 (核心参数)
        params = DirpMeasurementParams()
        ret = sdk.dirp_get_measurement_params(handle, byref(params))
        if ret == 0:
            result["measurement_params"] = {
                "emissivity": params.emissivity,           # 发射率
                "distance": params.distance,               # 目标距离 (米)
                "humidity": params.humidity,               # 相对湿度 (%)
                "reflection": params.reflection,           # 反射温度 (摄氏度)
                "ambient_temp": params.ambient_temp,       # 环境温度 (摄氏度)
            }
        else:
            result["measurement_params_error"] = f"获取失败, 错误码: {ret}"
        
        # 获取参数范围
        params_range = DirpMeasurementParamsRange()
        ret = sdk.dirp_get_measurement_params_range(handle, byref(params_range))
        if ret == 0:
            result["params_range"] = {
                "distance": {"min": params_range.distance.min, "max": params_range.distance.max},
                "humidity": {"min": params_range.humidity.min, "max": params_range.humidity.max},
                "emissivity": {"min": params_range.emissivity.min, "max": params_range.emissivity.max},
                "reflection": {"min": params_range.reflection.min, "max": params_range.reflection.max},
                "ambient_temp": {"min": params_range.ambient_temp.min, "max": params_range.ambient_temp.max},
            }
        
        return result
        
    finally:
        # 销毁句柄
        sdk.dirp_destroy(handle)


# ==================== 格式化输出 ====================
def print_params(params: dict):
    """格式化打印参数"""
    print("=" * 60)
    print(f"文件: {params.get('file_name', 'N/A')}")
    print("=" * 60)
    
    if "resolution" in params:
        res = params["resolution"]
        print(f"\n【图像分辨率】")
        print(f"  宽度: {res['width']} px")
        print(f"  高度: {res['height']} px")
    
    if "rjpeg_version" in params:
        ver = params["rjpeg_version"]
        print(f"\n【R-JPEG 版本】")
        print(f"  RJPEG: {ver['rjpeg']}")
        print(f"  Header: {ver['header']}")
        print(f"  Curve: {ver['curve']}")
    
    if "measurement_params" in params:
        mp = params["measurement_params"]
        print(f"\n【测量参数】")
        print(f"  发射率 (Emissivity):     {mp['emissivity']:.2f}")
        print(f"  目标距离 (Distance):     {mp['distance']:.2f} m")
        print(f"  相对湿度 (Humidity):     {mp['humidity']:.1f} %")
        print(f"  反射温度 (Reflection):   {mp['reflection']:.2f} °C")
        print(f"  环境温度 (Ambient):      {mp['ambient_temp']:.2f} °C")
    
    if "params_range" in params:
        pr = params["params_range"]
        print(f"\n【参数有效范围】")
        print(f"  距离:     [{pr['distance']['min']:.1f}, {pr['distance']['max']:.1f}] m")
        print(f"  湿度:     [{pr['humidity']['min']:.1f}, {pr['humidity']['max']:.1f}] %")
        print(f"  发射率:   [{pr['emissivity']['min']:.2f}, {pr['emissivity']['max']:.2f}]")
        print(f"  反射温度: [{pr['reflection']['min']:.1f}, {pr['reflection']['max']:.1f}] °C")
        print(f"  环境温度: [{pr['ambient_temp']['min']:.1f}, {pr['ambient_temp']['max']:.1f}] °C")
    
    print("=" * 60)


# ==================== 主函数 ====================
def main():
    if len(sys.argv) < 2:
        print("用法: python get_thermal_params.py <图像路径>")
        print("示例: python get_thermal_params.py input_dir/DJI_20251126161006_0001_T.JPG")
        sys.exit(1)
    
    image_path = sys.argv[1]
    
    try:
        params = get_thermal_params(image_path)
        print_params(params)
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
