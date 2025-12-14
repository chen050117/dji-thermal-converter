# encoding: utf-8
# DJI Thermal SDK v1.8 热成像图像批量转换工具
# 支持自动读取图像参数或手动指定参数

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
import argparse
from dataclasses import dataclass
from typing import Optional

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
    use_image_params: bool = True           # 是否自动读取图像参数
    manual_params: Optional[ThermalParams] = None  # 手动指定的参数

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
    # 转换为绝对路径
    input_abs = os.path.abspath(input_path)
    output_abs = os.path.abspath(output_path)
    exe_abs = os.path.abspath(SDK_EXE_PATH)
    
    # 构建命令参数列表
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
    
    # 执行命令并等待完成
    result = subprocess.run(cmd_args, capture_output=True, text=True)
    
    # 检查输出文件是否生成
    if not os.path.exists(output_abs):
        raise RuntimeError(f"SDK转换失败: {result.stderr or result.stdout or '未知错误'}")

def process_single_image(input_path: str, temp_dir: str, output_dir: str, 
                         use_image_params: bool, manual_params: Optional[ThermalParams]):
    """处理单张图片"""
    try:
        img_name = os.path.basename(input_path)
        base_name = os.path.splitext(img_name)[0]
        thread_id = threading.get_ident()
        
        raw_path = os.path.join(temp_dir, f"{base_name}_{thread_id}.raw")
        tiff_path = os.path.join(output_dir, f"{base_name}.tiff")
        
        # 获取参数
        if use_image_params:
            params = read_params_from_image(input_path)
        else:
            params = manual_params
        
        # 转换为RAW
        convert_to_raw(input_path, raw_path, params)
        
        # 读取图像尺寸
        image = Image.open(input_path)
        width, height = image.size
        
        # 读取RAW数据并转换
        img_data = np.fromfile(raw_path, dtype='int16')
        img_data = img_data / 10.0  # RAW值是实际温度的10倍
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

def run(config: ProcessConfig):
    """主运行函数"""
    print("=" * 60)
    print("DJI Thermal SDK v1.8 热成像批量转换工具")
    print("=" * 60)
    
    # 创建目录
    temp_dir = "temp_dir"
    mkdir(temp_dir)
    mkdir(config.output_dir)
    
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
    
    if config.use_image_params:
        print(f"参数模式: 自动读取图像嵌入参数")
        # 显示首张图像参数
        try:
            sample_params = read_params_from_image(input_files[0])
            print(f"首张图像参数: {sample_params}")
        except Exception as e:
            print(f"读取首张图像参数失败: {e}")
    else:
        print(f"参数模式: 手动指定参数")
        print(f"使用参数: {config.manual_params}")
    
    print("\n开始处理...")
    
    # 多线程处理
    success_count = 0
    failed_files = []
    
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = {
            executor.submit(
                process_single_image, f, temp_dir, config.output_dir,
                config.use_image_params, config.manual_params
            ): f for f in input_files
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
    
    直接修改下面的参数配置，然后运行 python main2.py 即可
    
    【两种参数模式】
    
    1. 自动模式 (USE_MANUAL_PARAMS = False)
       - 程序自动从每张图像中读取嵌入的测量参数
       - 每张图像使用其自身拍摄时的参数进行温度计算
       - 适用于: 不同图像拍摄条件不同的情况
    
    2. 手动模式 (USE_MANUAL_PARAMS = True)
       - 使用下面手动指定的统一参数
       - 所有图像使用相同的参数进行温度计算
       - 适用于: 需要统一参数或覆盖原始参数的情况
    
    【测量参数说明】
    
    - emissivity  : 发射率，被测物体表面辐射能力，范围 [0.10, 1.00]
    - distance    : 目标距离 (米)，待测目标的距离，范围 [1.0, 300.0]
    - humidity    : 相对湿度 (%)，环境空气湿度，范围 [1.0, 100.0]
    - reflection  : 反射温度 (°C)，周围环境反射温度，范围 [-40.0, 100.0]
    - ambient     : 环境温度 (°C)，大气环境温度，范围 [-40.0, 80.0]
    
    =================================================
    """
    
    # ==================== 基础配置 ====================
    INPUT_DIR = "input_dir"       # 输入文件夹路径
    OUTPUT_DIR = "out_dir"        # 输出文件夹路径
    MAX_WORKERS = 10              # 并行处理线程数
    
    # ==================== 模式选择 ====================
    # False = 自动模式: 从每张图像自动读取嵌入的参数
    # True  = 手动模式: 使用下面手动指定的统一参数
    USE_MANUAL_PARAMS = False
    
    # ==================== 手动模式参数 ====================
    # 仅当 USE_MANUAL_PARAMS = True 时生效
    MANUAL_EMISSIVITY = 0.95      # 发射率 [0.10, 1.00]
    MANUAL_DISTANCE = 13.0        # 目标距离 (米) [1.0, 300.0]
    MANUAL_HUMIDITY = 50.0        # 相对湿度 (%) [1.0, 100.0]
    MANUAL_REFLECTION = 25.0      # 反射温度 (°C) [-40.0, 100.0]
    MANUAL_AMBIENT = 22.0         # 环境温度 (°C) [-40.0, 80.0]
    
    # ==================== 构建配置并运行 ====================
    if USE_MANUAL_PARAMS:
        # 手动模式
        config = ProcessConfig(
            input_dir=INPUT_DIR,
            output_dir=OUTPUT_DIR,
            max_workers=MAX_WORKERS,
            use_image_params=False,
            manual_params=ThermalParams(
                emissivity=MANUAL_EMISSIVITY,
                distance=MANUAL_DISTANCE,
                humidity=MANUAL_HUMIDITY,
                reflection=MANUAL_REFLECTION,
                ambient=MANUAL_AMBIENT,
            ),
        )
    else:
        # 自动模式
        config = ProcessConfig(
            input_dir=INPUT_DIR,
            output_dir=OUTPUT_DIR,
            max_workers=MAX_WORKERS,
            use_image_params=True,
            manual_params=None,
        )
    
    # 执行处理
    run(config)
