# encoding: utf-8
# DJI Thermal SDK v1.8 热成像图像批量转换工具
# 支持自动、手动、半自动三种参数模式

import os
import sys
import shutil
import platform
import subprocess
import ctypes
from ctypes import c_int32, c_uint8, c_float, c_void_p, POINTER, Structure, byref
import piexif
import numpy as np
from tqdm import tqdm
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict

# ==================== 配置参数 ====================
@dataclass
class ThermalParams:
    """热成像测量参数"""
    emissivity: float = 0.95      # 发射率 [0.10, 1.00]
    distance: float = 5.0         # 目标距离 (m) [1.0, 300.0]
    humidity: float = 70.0        # 相对湿度 (%) [1.0, 100.0]
    reflection: float = 25.0      # 反射温度 (°C) [-40.0, 100.0]
    ambient: float = 25.0         # 环境温度 (°C) [-40.0, 80.0]
    
    def to_dict(self):
        return {
            "emissivity": self.emissivity,
            "distance": self.distance,
            "humidity": self.humidity,
            "reflection": self.reflection,
            "ambient": self.ambient,
        }
    
    def __str__(self):
        return (f"发射率: {self.emissivity:.2f}, 距离: {self.distance:.2f}m, "
                f"湿度: {self.humidity:.1f}%, 反射温度: {self.reflection:.2f}°C, "
                f"环境温度: {self.ambient:.2f}°C")


@dataclass 
class ProcessConfig:
    """处理配置"""
    input_dir: str                          # 输入文件夹路径
    output_dir: str                         # 输出文件夹路径
    max_workers: int = 10                   # 最大线程数
    
    # 参数模式: "auto" | "manual" | "semi"
    # auto   = 全部从图像自动读取
    # manual = 全部使用手动参数
    # semi   = 半自动模式，部分自动读取，部分手动覆盖
    param_mode: str = "auto"
    
    # 手动参数 (manual 和 semi 模式使用)
    manual_params: Optional[ThermalParams] = None
    
    # 备用参数 (当图像参数无法读取时使用)
    fallback_params: Optional[ThermalParams] = None
    
    # 半自动模式: 指定哪些参数使用手动值覆盖
    # 可选: "emissivity", "distance", "humidity", "reflection", "ambient"
    override_params: Dict[str, bool] = field(default_factory=lambda: {
        "emissivity": False,
        "distance": False,
        "humidity": False,
        "reflection": False,
        "ambient": False,
    })


# ==================== SDK DLL 相关 ====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SDK_DLL_DIR = os.path.join(SCRIPT_DIR, "dji_thermal_sdk_v1.8_20250829", "tsdk-core", "lib", "windows", "release_x64")
SDK_EXE_PATH = os.path.join(SCRIPT_DIR, "dji_thermal_sdk_v1.8_20250829", "utility", "bin", "windows", "release_x64", "dji_irp.exe")


class DirpMeasurementParams(Structure):
    """SDK测量参数结构体"""
    _fields_ = [
        ("distance", c_float),
        ("humidity", c_float),
        ("emissivity", c_float),
        ("reflection", c_float),
        ("ambient_temp", c_float),
    ]


_sdk_lock = threading.Lock()
_sdk_instance = None


def get_sdk():
    """获取SDK实例（单例，线程安全）"""
    global _sdk_instance
    if _sdk_instance is None:
        with _sdk_lock:
            if _sdk_instance is None:
                if hasattr(os, 'add_dll_directory'):
                    os.add_dll_directory(SDK_DLL_DIR)
                os.environ['PATH'] = SDK_DLL_DIR + os.pathsep + os.environ.get('PATH', '')
                
                dll_path = os.path.join(SDK_DLL_DIR, "libdirp.dll")
                if not os.path.exists(dll_path):
                    raise FileNotFoundError(f"SDK DLL 未找到: {dll_path}")
                
                sdk = ctypes.CDLL(dll_path)
                sdk.dirp_create_from_rjpeg.argtypes = [POINTER(c_uint8), c_int32, POINTER(c_void_p)]
                sdk.dirp_create_from_rjpeg.restype = c_int32
                sdk.dirp_destroy.argtypes = [c_void_p]
                sdk.dirp_destroy.restype = c_int32
                sdk.dirp_get_measurement_params.argtypes = [c_void_p, POINTER(DirpMeasurementParams)]
                sdk.dirp_get_measurement_params.restype = c_int32
                _sdk_instance = sdk
    return _sdk_instance


def read_params_from_image(image_path: str) -> ThermalParams:
    """从图像中读取嵌入的测量参数"""
    sdk = get_sdk()
    
    with open(image_path, 'rb') as f:
        data = f.read()
    
    buf = (c_uint8 * len(data)).from_buffer_copy(data)
    handle = c_void_p()
    ret = sdk.dirp_create_from_rjpeg(buf, len(data), byref(handle))
    if ret != 0:
        raise RuntimeError(f"创建DIRP句柄失败, 错误码: {ret}")
    
    try:
        params = DirpMeasurementParams()
        ret = sdk.dirp_get_measurement_params(handle, byref(params))
        if ret != 0:
            raise RuntimeError(f"获取测量参数失败, 错误码: {ret}")
        
        return ThermalParams(
            emissivity=params.emissivity,
            distance=params.distance,
            humidity=params.humidity,
            reflection=params.reflection,
            ambient=params.ambient_temp,
        )
    finally:
        sdk.dirp_destroy(handle)


def merge_params(image_params: ThermalParams, manual_params: ThermalParams, 
                 override: Dict[str, bool]) -> ThermalParams:
    """
    合并参数 (半自动模式)
    根据 override 配置决定每个参数使用图像值还是手动值
    """
    return ThermalParams(
        emissivity=manual_params.emissivity if override.get("emissivity", False) else image_params.emissivity,
        distance=manual_params.distance if override.get("distance", False) else image_params.distance,
        humidity=manual_params.humidity if override.get("humidity", False) else image_params.humidity,
        reflection=manual_params.reflection if override.get("reflection", False) else image_params.reflection,
        ambient=manual_params.ambient if override.get("ambient", False) else image_params.ambient,
    )


# ==================== 核心处理函数 ====================
def get_platform():
    return platform.system()


def mkdir(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return True


def convert_to_raw(input_path: str, output_path: str, params: ThermalParams):
    """调用SDK将R-JPEG转换为RAW温度数据"""
    input_abs = os.path.abspath(input_path)
    output_abs = os.path.abspath(output_path)
    exe_abs = os.path.abspath(SDK_EXE_PATH)
    
    cmd_args = [
        exe_abs,
        "-s", input_abs,
        "-a", "measure",
        "-o", output_abs,
        "--emissivity", str(params.emissivity),
        "--distance", str(params.distance),
        "--humidity", str(params.humidity),
        "--reflection", str(params.reflection),
        "--ambient", str(params.ambient),
    ]
    
    result = subprocess.run(cmd_args, capture_output=True, text=True)
    
    if not os.path.exists(output_abs):
        raise RuntimeError(f"SDK转换失败: {result.stderr or result.stdout or '未知错误'}")


def process_single_image(input_path: str, temp_dir: str, output_dir: str, config: ProcessConfig):
    """处理单张图片"""
    try:
        img_name = os.path.basename(input_path)
        base_name = os.path.splitext(img_name)[0]
        thread_id = threading.get_ident()
        
        raw_path = os.path.join(temp_dir, f"{base_name}_{thread_id}.raw")
        tiff_path = os.path.join(output_dir, f"{base_name}.tiff")
        
        # 根据模式获取参数
        if config.param_mode == "auto":
            # 自动模式: 全部从图像读取
            try:
                params = read_params_from_image(input_path)
            except Exception:
                # 如果读取失败，使用备用参数
                if config.fallback_params:
                    params = config.fallback_params
                else:
                    raise
        elif config.param_mode == "manual":
            # 手动模式: 全部使用手动参数
            params = config.manual_params
        else:
            # 半自动模式: 部分自动，部分手动覆盖
            try:
                image_params = read_params_from_image(input_path)
                params = merge_params(image_params, config.manual_params, config.override_params)
            except Exception:
                # 如果读取失败，使用备用参数或手动参数
                if config.fallback_params:
                    params = config.fallback_params
                elif config.manual_params:
                    params = config.manual_params
                else:
                    raise
        
        # 转换为RAW
        convert_to_raw(input_path, raw_path, params)
        
        # 读取图像尺寸
        image = Image.open(input_path)
        width, height = image.size
        
        # 读取RAW数据并转换
        img_data = np.fromfile(raw_path, dtype='int16')
        img_data = img_data / 10.0
        img_data = img_data.reshape(height, width)
        
        # 保存为TIFF
        im = Image.fromarray(img_data)
        exif_dict = piexif.load(input_path)
        new_exif = {
            '0th': {}, 'Exif': {}, 'GPS': exif_dict.get('GPS', {}),
            'Interop': {}, '1st': {}, 'thumbnail': exif_dict.get('thumbnail', None)
        }
        exif_bytes = piexif.dump(new_exif)
        im.save(tiff_path, exif=exif_bytes)
        
        # 清理临时文件
        if os.path.exists(raw_path):
            os.remove(raw_path)
        
        return True, input_path, params
    except Exception as e:
        return False, f"{input_path}: {str(e)}", None


def check_params_readable(input_files: list, config: ProcessConfig) -> tuple:
    """
    检查图像参数是否可读取
    返回: (可读取的文件列表, 不可读取的文件列表, 首张可读取图像的参数)
    """
    readable_files = []
    unreadable_files = []
    first_params = None
    
    print("\n正在检查图像参数可读性...")
    
    # 检查前几张图像（最多检查50张作为采样，均匀分布）
    total_files = len(input_files)
    sample_count = min(total_files, 50)
    
    # 均匀采样
    if total_files <= sample_count:
        sample_indices = list(range(total_files))
    else:
        step = total_files / sample_count
        sample_indices = [int(i * step) for i in range(sample_count)]
    
    print(f"采样检查 {len(sample_indices)} 张图像...")
    
    for idx in sample_indices:
        file_path = input_files[idx]
        try:
            params = read_params_from_image(file_path)
            readable_files.append(file_path)
            if first_params is None:
                first_params = params
        except Exception as e:
            unreadable_files.append((file_path, str(e)))
    
    # 如果采样全部成功，假设其余文件也可读
    if len(unreadable_files) == 0:
        readable_files = input_files
        print(f"采样检查通过 ({sample_count}/{sample_count} 成功)")
    else:
        print(f"采样检查完成: {len(readable_files)} 成功, {len(unreadable_files)} 失败")
    
    return readable_files, unreadable_files, first_params


def prompt_for_manual_params() -> ThermalParams:
    """提示用户输入手动参数"""
    print("\n" + "=" * 60)
    print("请输入测量参数 (直接回车使用默认值)")
    print("=" * 60)
    
    def get_float_input(prompt: str, default: float, min_val: float, max_val: float) -> float:
        while True:
            try:
                user_input = input(f"{prompt} [{default}]: ").strip()
                if user_input == "":
                    return default
                value = float(user_input)
                if min_val <= value <= max_val:
                    return value
                else:
                    print(f"  错误: 值必须在 [{min_val}, {max_val}] 范围内")
            except ValueError:
                print("  错误: 请输入有效的数字")
    
    emissivity = get_float_input("发射率 (Emissivity) [0.10-1.00]", 0.95, 0.10, 1.00)
    distance = get_float_input("目标距离 (Distance) [1.0-300.0] m", 5.0, 1.0, 300.0)
    humidity = get_float_input("相对湿度 (Humidity) [1.0-100.0] %", 70.0, 1.0, 100.0)
    reflection = get_float_input("反射温度 (Reflection) [-40.0-100.0] °C", 25.0, -40.0, 100.0)
    ambient = get_float_input("环境温度 (Ambient) [-40.0-80.0] °C", 25.0, -40.0, 80.0)
    
    return ThermalParams(
        emissivity=emissivity,
        distance=distance,
        humidity=humidity,
        reflection=reflection,
        ambient=ambient,
    )


def run(config: ProcessConfig):
    """主运行函数"""
    print("=" * 60)
    print("DJI Thermal SDK v1.8 热成像批量转换工具")
    print("=" * 60)
    
    # 获取文件列表
    input_files = []
    for root, _, files in os.walk(config.input_dir):
        for f in files:
            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                input_files.append(os.path.join(root, f))
    
    if not input_files:
        raise ValueError(f"在 {config.input_dir} 中未找到图像文件")
    
    print(f"\n输入目录: {config.input_dir}")
    print(f"输出目录: {config.output_dir}")
    print(f"检测到文件: {len(input_files)} 个")
    print(f"线程数: {config.max_workers}")
    
    # ==================== 参数检查与确认 ====================
    if config.param_mode in ["auto", "semi"]:
        # 自动或半自动模式需要检查图像参数是否可读
        readable_files, unreadable_files, first_params = check_params_readable(input_files, config)
        
        if unreadable_files:
            # 有文件无法读取参数
            print("\n" + "!" * 60)
            print("警告: 以下图像无法读取嵌入参数:")
            print("!" * 60)
            for file_path, error in unreadable_files[:5]:
                print(f"  - {os.path.basename(file_path)}: {error}")
            if len(unreadable_files) > 5:
                print(f"  ... 还有 {len(unreadable_files) - 5} 个文件")
            
            print("\n" + "-" * 60)
            print("【需要的测量参数】")
            print("-" * 60)
            print("  1. 发射率 (Emissivity)   : 被测物体表面辐射能力 [0.10-1.00]")
            print("  2. 目标距离 (Distance)   : 相机到目标的距离 [1.0-300.0] m")
            print("  3. 相对湿度 (Humidity)   : 环境空气湿度 [1.0-100.0] %")
            print("  4. 反射温度 (Reflection) : 周围环境反射温度 [-40.0-100.0] °C")
            print("  5. 环境温度 (Ambient)    : 大气环境温度 [-40.0-80.0] °C")
            print("-" * 60)
            
            print("\n请选择处理方式:")
            print("  [1] 跳过无法读取的文件，继续处理其他文件")
            print("  [2] 手动输入参数，用于无法读取的文件")
            print("  [3] 切换到手动模式，所有文件使用统一参数")
            print("  [4] 取消操作")
            
            while True:
                choice = input("\n请输入选项 (1/2/3/4): ").strip()
                if choice == "1":
                    # 跳过无法读取的文件
                    input_files = readable_files
                    print(f"\n将跳过 {len(unreadable_files)} 个无法读取的文件")
                    break
                elif choice == "2":
                    # 手动输入参数用于无法读取的文件
                    fallback_params = prompt_for_manual_params()
                    config.fallback_params = fallback_params
                    print(f"\n无法读取参数的文件将使用: {fallback_params}")
                    break
                elif choice == "3":
                    # 切换到手动模式
                    config.param_mode = "manual"
                    if config.manual_params is None:
                        config.manual_params = prompt_for_manual_params()
                    print(f"\n已切换到手动模式，所有文件使用: {config.manual_params}")
                    break
                elif choice == "4":
                    print("\n操作已取消")
                    return
                else:
                    print("无效选项，请重新输入")
        
        elif first_params:
            # 所有文件都可以读取参数
            print(f"\n参数检查通过，所有图像参数可读取")
            print(f"首张图像参数: {first_params}")
    
    # 显示最终参数模式
    print("\n" + "-" * 60)
    if config.param_mode == "auto":
        print(f"参数模式: 自动模式 (全部从图像读取)")
    elif config.param_mode == "manual":
        print(f"参数模式: 手动模式 (全部使用手动参数)")
        print(f"手动参数: {config.manual_params}")
    else:
        print(f"参数模式: 半自动模式 (部分自动，部分手动覆盖)")
        override_list = [k for k, v in config.override_params.items() if v]
        auto_list = [k for k, v in config.override_params.items() if not v]
        print(f"  手动覆盖: {', '.join(override_list) if override_list else '无'}")
        print(f"  自动读取: {', '.join(auto_list) if auto_list else '无'}")
        if override_list:
            print(f"  手动参数值:")
            mp = config.manual_params
            if config.override_params.get("emissivity"): print(f"    - 发射率: {mp.emissivity:.2f}")
            if config.override_params.get("distance"): print(f"    - 距离: {mp.distance:.2f} m")
            if config.override_params.get("humidity"): print(f"    - 湿度: {mp.humidity:.1f} %")
            if config.override_params.get("reflection"): print(f"    - 反射温度: {mp.reflection:.2f} °C")
            if config.override_params.get("ambient"): print(f"    - 环境温度: {mp.ambient:.2f} °C")
    print("-" * 60)
    
    # 确认继续
    confirm = input("\n确认开始处理? (y/n): ").strip().lower()
    if confirm != 'y':
        print("操作已取消")
        return
    
    # 创建目录
    temp_dir = "temp_dir"
    mkdir(temp_dir)
    mkdir(config.output_dir)
    
    print("\n开始处理...")
    
    # 多线程处理
    success_count = 0
    failed_files = []
    
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = {
            executor.submit(process_single_image, f, temp_dir, config.output_dir, config): f
            for f in input_files
        }
        
        with tqdm(total=len(input_files), desc="转换进度") as pbar:
            for future in as_completed(futures):
                success, result, _ = future.result()
                if success:
                    success_count += 1
                else:
                    failed_files.append(result)
                pbar.update(1)
    
    # 清理
    shutil.rmtree(temp_dir)
    
    # 结果统计
    print(f"\n处理完成: {success_count}/{len(input_files)} 成功")
    if failed_files:
        print(f"失败文件 ({len(failed_files)}):")
        for f in failed_files[:5]:
            print(f"  - {f}")
        if len(failed_files) > 5:
            print(f"  ... 还有 {len(failed_files) - 5} 个")


if __name__ == "__main__":
    """
    ==================== 使用说明 ====================
    
    【三种参数模式】
    
    1. 自动模式 (PARAM_MODE = "auto")
       - 全部参数从每张图像自动读取
       - 每张图像使用其自身拍摄时的参数
       - 适用于: 信任图像原始参数的情况
    
    2. 手动模式 (PARAM_MODE = "manual")
       - 全部参数使用手动指定的统一值
       - 所有图像使用相同的参数
       - 适用于: 需要完全覆盖原始参数的情况
    
    3. 半自动模式 (PARAM_MODE = "semi")
       - 部分参数从图像自动读取
       - 部分参数使用手动指定的值覆盖
       - 通过 OVERRIDE_* 开关控制每个参数
       - 适用于: 只需要修改部分参数的情况
    
    【测量参数说明】
    
    - emissivity  : 发射率，范围 [0.10, 1.00]
    - distance    : 目标距离 (米)，范围 [1.0, 300.0]
    - humidity    : 相对湿度 (%)，范围 [1.0, 100.0]
    - reflection  : 反射温度 (°C)，范围 [-40.0, 100.0]
    - ambient     : 环境温度 (°C)，范围 [-40.0, 80.0]
    
    =================================================
    """
    
    # ==================== 基础配置 ====================
    INPUT_DIR = "input_dir"       # 输入文件夹路径
    OUTPUT_DIR = "out_dir"        # 输出文件夹路径
    MAX_WORKERS = 10              # 并行处理线程数
    
    # ==================== 模式选择 ====================
    # "auto"   = 自动模式: 全部参数从图像自动读取
    # "manual" = 手动模式: 全部参数使用手动指定值
    # "semi"   = 半自动模式: 部分自动，部分手动覆盖
    PARAM_MODE = "auto"
    
    # ==================== 手动参数值 ====================
    # manual 模式: 全部使用这些值
    # semi 模式: 只有 OVERRIDE_*=True 的参数使用这些值
    MANUAL_EMISSIVITY = 0.95      # 发射率 [0.10, 1.00]
    MANUAL_DISTANCE = 13.0        # 目标距离 (米) [1.0, 300.0]
    MANUAL_HUMIDITY = 61.0        # 相对湿度 (%) [1.0, 100.0]
    MANUAL_REFLECTION = 25.0      # 反射温度 (°C) [-40.0, 100.0]
    MANUAL_AMBIENT = 22.0         # 环境温度 (°C) [-40.0, 80.0]
    
    # ==================== 半自动模式覆盖开关 ====================
    # 仅当 PARAM_MODE = "semi" 时生效
    # True  = 使用上面的手动值覆盖
    # False = 从图像自动读取
    OVERRIDE_EMISSIVITY = False   # 是否覆盖发射率
    OVERRIDE_DISTANCE = False     # 是否覆盖目标距离
    OVERRIDE_HUMIDITY = False     # 是否覆盖相对湿度
    OVERRIDE_REFLECTION = False   # 是否覆盖反射温度
    OVERRIDE_AMBIENT = False      # 是否覆盖环境温度
    
    # ==================== 构建配置并运行 ====================
    config = ProcessConfig(
        input_dir=INPUT_DIR,
        output_dir=OUTPUT_DIR,
        max_workers=MAX_WORKERS,
        param_mode=PARAM_MODE,
        manual_params=ThermalParams(
            emissivity=MANUAL_EMISSIVITY,
            distance=MANUAL_DISTANCE,
            humidity=MANUAL_HUMIDITY,
            reflection=MANUAL_REFLECTION,
            ambient=MANUAL_AMBIENT,
        ),
        override_params={
            "emissivity": OVERRIDE_EMISSIVITY,
            "distance": OVERRIDE_DISTANCE,
            "humidity": OVERRIDE_HUMIDITY,
            "reflection": OVERRIDE_REFLECTION,
            "ambient": OVERRIDE_AMBIENT,
        },
    )
    
    # 执行处理
    run(config)
