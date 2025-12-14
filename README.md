# DJI Thermal Image Converter

基于 DJI Thermal SDK v1.8 的热成像图像批量转换工具，将 DJI 无人机拍摄的 R-JPEG 热成像图像转换为包含真实温度值的 TIFF 文件。

## 功能特点

- 🚀 **多线程并行处理**：支持多线程批量转换，处理速度快
- 📊 **自动参数读取**：自动从图像中提取嵌入的测量参数（发射率、距离、湿度、反射温度、环境温度）
- 🎛️ **手动参数覆盖**：支持手动指定统一的测量参数
- 📍 **GPS 信息保留**：转换后的 TIFF 文件保留原始 GPS 坐标信息
- 🌡️ **真实温度值**：输出的 TIFF 文件每个像素值代表实际温度（摄氏度）

## 支持设备

- Zenmuse H20T / H20N / H30T
- Zenmuse XT S
- Mavic 2 Enterprise Advanced (M2EA)
- Matrice 30T (M30T)
- Matrice 3T / 3TD (M3T / M3TD)
- Matrice 4T (M4T)

## 环境要求

- Windows 10/11 (64-bit)
- Python 3.8+
- DJI Thermal SDK v1.8

## 安装

1. 克隆仓库
```bash
git https://github.com/chen050117/dji-thermal-converter.git
cd dji-thermal-converter
```

2. 安装 Python 依赖
```bash
pip install numpy pillow piexif tqdm
```

3. 下载 DJI Thermal SDK v1.8
   - 从 [DJI 开发者官网](https://www.dji.com/cn/downloads/softwares/dji-thermal-sdk) 下载 SDK
   - 解压到项目目录，确保目录结构如下：
```
dji-thermal-converter/
├── main.py
├── dji_thermal_sdk_v1.8_20250829/
│   ├── tsdk-core/
│   │   ├── api/
│   │   └── lib/
│   │       └── windows/
│   │           └── release_x64/
│   │               └── libdirp.dll
│   └── utility/
│       └── bin/
│           └── windows/
│               └── release_x64/
│                   └── dji_irp.exe
├── input_dir/          # 输入图像目录
└── out_dir/            # 输出目录
```

## 使用方法

### 快速开始

1. 将热成像图像放入 `input_dir` 目录
2. 修改 `main.py` 中的配置参数
3. 运行程序
```bash
python main.py
```

### 配置说明

在 `main.py` 文件底部修改以下配置：

```python
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
```

### 两种参数模式

| 模式 | 设置 | 说明 |
|------|------|------|
| 自动模式 | `USE_MANUAL_PARAMS = False` | 从每张图像自动读取嵌入的测量参数 |
| 手动模式 | `USE_MANUAL_PARAMS = True` | 所有图像使用统一的手动指定参数 |

### 测量参数说明

| 参数 | 说明 | 范围 |
|------|------|------|
| emissivity | 发射率，被测物体表面辐射能力 | [0.10, 1.00] |
| distance | 目标距离 (米) | [1.0, 300.0] |
| humidity | 相对湿度 (%) | [1.0, 100.0] |
| reflection | 反射温度 (°C)，周围环境反射温度 | [-40.0, 100.0] |
| ambient | 环境温度 (°C)，大气环境温度 | [-40.0, 80.0] |

## 输出格式

- 输出文件格式：32-bit TIFF
- 像素值：实际温度值（摄氏度）
- 元数据：保留原始 GPS 坐标信息

## 运行示例

```
============================================================
DJI Thermal SDK v1.8 热成像批量转换工具
============================================================

输入目录: input_dir
输出目录: out_dir
检测到文件: 407 个
线程数: 10
参数模式: 自动读取图像嵌入参数
首张图像参数: 发射率: 0.95, 距离: 13.00m, 湿度: 50.0%, 反射温度: 25.00°C, 环境温度: 22.12°C

开始处理...
转换进度: 100%|████████████████████████████████| 407/407 [00:16<00:00, 24.29it/s]

处理完成: 407/407 成功
```

## 项目结构

```
dji-thermal-converter/
├── main.py                     # 主程序 - 批量转换工具
├── get_thermal_params.py       # 参数读取工具 - 查看图像嵌入参数
├── README.md                   # 说明文档
├── requirements.txt            # Python 依赖
├── dji_thermal_sdk_v1.8_20250829/  # DJI Thermal SDK
├── input_dir/                  # 输入图像目录
└── out_dir/                    # 输出目录
```

## 参数读取工具

`get_thermal_params.py` 可以读取单张热成像图像中嵌入的测量参数：

```bash
python get_thermal_params.py <图像路径>
```

示例：
```bash
python get_thermal_params.py input_dir/DJI_20251126161006_0001_T.JPG
```

输出示例：
```
============================================================
文件: DJI_20251126161006_0001_T.JPG
============================================================

【图像分辨率】
  宽度: 1280 px
  高度: 1024 px

【R-JPEG 版本】
  RJPEG: 0x300
  Header: 0x1
  Curve: 0x1

【测量参数】
  发射率 (Emissivity):     0.95
  目标距离 (Distance):     13.00 m
  相对湿度 (Humidity):     50.0 %
  反射温度 (Reflection):   25.00 °C
  环境温度 (Ambient):      22.12 °C

【参数有效范围】
  距离:     [1.0, 300.0] m
  湿度:     [1.0, 100.0] %
  发射率:   [0.10, 1.00]
  反射温度: [-40.0, 100.0] °C
  环境温度: [-40.0, 80.0] °C
============================================================
```

## 常见问题

**Q: 为什么转换失败？**
- 确保 DJI Thermal SDK 路径正确
- 确保输入图像是 DJI 热成像相机拍摄的 R-JPEG 格式
- 检查图像文件名不包含特殊字符

**Q: 如何查看图像的嵌入参数？**
- 使用 `get_thermal_params.py` 工具：
```bash
python get_thermal_params.py input_dir/your_image.JPG
```

**Q: 支持 Linux 吗？**
- 目前主要支持 Windows，Linux 需要使用对应的 SDK 库文件

## 参考资料

- [DJI Thermal SDK 官方文档](https://www.dji.com/cn/downloads/softwares/dji-thermal-sdk)
- [DJI 开发者论坛](https://forum.dji.com/)

## License

MIT License

## 致谢

- DJI Thermal SDK
- 感谢所有贡献者
