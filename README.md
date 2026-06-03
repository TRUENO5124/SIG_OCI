# STM32F103C8T6 简易信号发生器 + 示波器

本项目包含 3 项交付内容：

- `sig_oci.uvprojx`：可直接用 Keil 打开的 STM32F103C8T6 工程。
- `instrument_panel.html`：HTML 版上位机仪器面板，操作方式接近真实示波器和信号发生器。
- `mcu_simulator.py`：下位机单片机模拟器，网络端口号为 `7897`。

另外保留了 `host_app.py` 作为 Python/Tkinter 版备用上位机，但推荐使用 HTML 面板。

## 引脚分配

| 功能 | STM32F103C8T6 引脚 | 说明 |
| --- | --- | --- |
| 串口发送 | `PA9` / `USART1_TX` | 单片机向上位机发送数据 |
| 串口接收 | `PA10` / `USART1_RX` | 单片机接收上位机命令 |
| 信号源输出 | `PA6` / `TIM3_CH1` | PWM 信号输出 |
| 示波器输入 | `PA0` / `ADC1_IN0` | ADC 采样输入 |
| 状态灯 | `PC13` | 常见 Blue Pill 板载 LED，表示程序正在运行 |

## PA9 和 PA10 的作用

`PA9` 和 `PA10` 用来做上位机和单片机之间的串口通信，不负责输出波形，也不负责采集波形。

- `PA9` 是 `USART1_TX`，也就是单片机的串口发送脚。单片机会通过它把采样数据、状态信息、响应结果发回上位机。
- `PA10` 是 `USART1_RX`，也就是单片机的串口接收脚。上位机通过它向单片机发送控制命令，例如设置信号源波形、频率、幅度，或者请求采样。

## DAPmini 的作用

本项目改用 DAPmini，不再使用 ST-Link。

DAPmini 在这里有两个作用：

- 通过 SWD 给 STM32F103C8T6 烧录和调试程序。
- 通过 DAPmini 自带的 `TX/RX` 串口引脚和单片机 `PA9/PA10` 通信。

因此硬件上不需要额外的 USB-TTL 串口模块。DAPmini 插到电脑后，一般会出现一个用于烧录调试的 CMSIS-DAP 设备，以及一个用于串口通信的 COM 口。HTML 上位机点击 `连接 DAPmini 串口` 时，选择这个 DAPmini 对应的 COM 口即可。

真实硬件连接时：

DAPmini 的 SWD 下载调试线：

| DAPmini SWD 引脚 | STM32F103C8T6 |
| --- | --- |
| SWDIO | `PA13` / SWDIO |
| SWCLK | `PA14` / SWCLK |
| GND | GND |
| 3V3 / VTref | 3.3V |
| NRST | NRST，可选 |

DAPmini 的串口通信线：

| DAPmini 串口引脚 | STM32F103C8T6 |
| --- | --- |
| RX | `PA9` / `USART1_TX` |
| TX | `PA10` / `USART1_RX` |
| GND | GND |

也就是说，串口线要交叉连接：DAPmini 的 `RX` 接单片机发送脚 `PA9`，DAPmini 的 `TX` 接单片机接收脚 `PA10`。DAPmini 同时还能通过 SWD 给单片机下载程序。

## 信号源说明

STM32F103C8T6 本身没有片上 DAC，因此本项目使用 `PA6` 的 PWM 输出模拟信号源。

- 方波：直接由 PWM 输出。
- 正弦波、三角波、锯齿波：通过改变 PWM 占空比来模拟波形。
- 如果需要在真实示波器上看到更平滑的模拟波形，通常需要外部滤波电路；本任务要求“不增加电路”，所以项目中保留 PWM 形式。

如果要做硬件自测，可以用一根杜邦线把 `PA6` 接到 `PA0`，这样单片机输出的 PWM 信号可以被自己的 ADC 采到。

## Keil 下位机程序

使用步骤：

1. 用 Keil uVision 打开 `sig_oci.uvprojx`。
2. 选择目标 `Target 1`。
3. 点击 Build 编译工程。
4. 通过 DAPmini 下载到 STM32F103C8T6。

如果 Keil 没有自动识别 DAPmini，需要在 `Options for Target` 里手动设置：

1. 打开 `Debug` 页。
2. `Use` 选择 `CMSIS-DAP Debugger`。
3. 点击 `Settings`，确认接口选择 `SW`，也就是 SWD。
4. 打开 `Utilities` 页，选择 `Use Debug Driver` 后再下载。

DAPmini 的 SWD 下载调试和 `TX/RX` 串口通信是两条独立通道：SWD 负责烧录程序，`TX/RX` 负责程序运行后和 HTML 上位机交换数据。

工程主要文件：

- `main.c`：主程序，包含串口协议、PWM 信号源、ADC 示波采样。
- `Start/system_stm32f10x.c`：系统时钟初始化。
- `Start/startup_stm32f10x_md.s`：STM32F103C8T6 中容量启动文件。

工程已经配置：

- 芯片宏：`STM32F10X_MD`
- 头文件路径：`.\Start`

## HTML 上位机仪器面板

本项目推荐使用 `instrument_panel.html` 作为上位机软件。它不是命令输入框，而是一个类似真实仪器的操作面板：

- 示波器区域有屏幕网格、运行/停止、单次采样、Time/Div、Volt/Div、垂直位置、触发电平等控制。
- 信号发生器区域有波形选择、频率旋钮、幅度旋钮、偏置旋钮、输出开关。
- 用户可以点击按钮、拖动旋钮、滚轮调节，也可以直接在数值框中手动输入参数，不需要手动输入 `GEN`、`CAP` 等底层命令。
- `Time/Div` 支持手动输入，最小可到 `1 ms/div`。
- 采样等待时间会根据采样点数和采样率自动延长；如果真实硬件没有响应导致超时，运行模式会自动停止，避免错误日志连续刷屏。

### 连接模拟器

最简单的方式是直接双击：

```text
run_instrument_panel.bat
```

这个脚本会自动启动 `mcu_simulator.py`，打开 `instrument_panel.html`，并让模拟器监听 `127.0.0.1:7897`。页面打开后点击右上角 `连接模拟器` 即可。

### 连接真实开发板

真实开发板不需要启动模拟器。双击 `run_instrument_panel.bat` 打开页面后，右上角点击 `连接 DAPmini 串口`，在浏览器弹出的串口列表中选择 DAPmini 对应的 COM 口。

如果只想打开面板、不启动模拟器，可以运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_instrument_panel.ps1 -NoSimulator
```

### 手动连接模拟器

先运行单片机模拟器：

```powershell
python mcu_simulator.py
```

然后用浏览器打开：

```text
instrument_panel.html
```

在页面右上角点击 `连接模拟器`。HTML 面板会通过 WebSocket 连接：

```text
ws://127.0.0.1:7897
```

连接成功后，可以直接操作界面：

1. 在信号发生器区域选择 `SINE`、`SQUARE`、`TRI` 或 `SAW`。
2. 拖动频率、幅度、偏置旋钮。
3. 点击 `输出 ON/OFF` 控制信号输出。
4. 在示波器区域点击 `运行` 或 `单次`。
5. 调节 `Time/Div`、`Volt/Div`、位置和触发电平观察波形。

### 手动连接真实开发板

真实硬件连接方式：

DAPmini 的 SWD 下载调试线：

| DAPmini SWD 引脚 | STM32F103C8T6 |
| --- | --- |
| SWDIO | `PA13` / SWDIO |
| SWCLK | `PA14` / SWCLK |
| GND | GND |
| 3V3 / VTref | 3.3V |
| NRST | NRST，可选 |

DAPmini 的串口通信线：

| DAPmini 串口引脚 | STM32F103C8T6 |
| --- | --- |
| RX | `PA9` / `USART1_TX` |
| TX | `PA10` / `USART1_RX` |
| GND | GND |

使用步骤：

1. 先用 Keil 编译并下载 `sig_oci.uvprojx` 到 STM32F103C8T6。
2. 使用 DAPmini 的 SWD 下载接口烧录程序。
3. 将 DAPmini 的串口 `RX/TX/GND` 按上表接到 `PA9/PA10/GND`。
4. 用 Chrome 或 Edge 打开 `instrument_panel.html`。
5. 点击右上角 `连接 DAPmini 串口`。
6. 浏览器弹出串口选择窗口后，选择 DAPmini 对应的串口 COM 口。
7. 连接成功后，直接用 HTML 面板操作示波器和信号发生器。

注意：浏览器串口功能依赖 Web Serial API，通常需要 Chrome 或 Edge。Firefox 一般不支持。

## Python 备用上位机

运行：

```powershell
python host_app.py
```

Python 备用上位机支持两种连接方式：

- `TCP simulator`：连接本机单片机模拟器，默认地址为 `127.0.0.1:7897`。
- `Serial board`：连接真实 STM32 开发板串口。

如果要连接真实硬件，并且电脑没有安装 `pyserial`，先执行：

```powershell
python -m pip install pyserial
```

然后在上位机界面中选择 `Serial board`，刷新串口，选择对应 COM 口并连接。

## 单片机模拟器

先运行模拟器：

```powershell
python mcu_simulator.py
```

模拟器会监听：

```text
127.0.0.1:7897
```

然后打开 `instrument_panel.html`，点击 `连接模拟器`，即可在没有真实开发板的情况下演示完整流程。

`mcu_simulator.py` 同时支持两种连接：

- WebSocket：给 `instrument_panel.html` 使用。
- 普通 TCP 文本协议：给 `host_app.py` 或调试脚本使用。

## 底层通信协议

HTML 面板已经把底层通信协议封装起来，正常使用时不需要手动输入命令。

底层仍然使用基于文本行的 ASCII 协议，命令以 `\n` 或 `\r\n` 结尾，便于调试和答辩说明。

### PING

命令：

```text
PING
```

响应：

```text
PONG STM32F103_SIGSCOPE
```

### INFO

命令：

```text
INFO
```

响应示例：

```text
INFO MCU=STM32F103C8T6 UART=USART1_115200 GEN=PA6_TIM3CH1_PWM SCOPE=PA0_ADC1IN0 GEN_HZ=1-10000 CAP_MAX=512 PORT=7897_SIM
```

### 设置信号源

命令格式：

```text
GEN <SINE|SQUARE|TRIANGLE|SAW> <频率Hz> <幅度百分比> <偏置百分比>
```

示例：

```text
GEN SINE 1000 70 50
```

关闭信号输出：

```text
GEN OFF
```

### 示波器采样

命令格式：

```text
CAP <采样点数> <采样率Hz>
```

示例：

```text
CAP 256 5000
```

响应格式：

```text
DATA 256 5000 3300
2048,2101,2150,...
END
```

其中 ADC 数据为 12 位数值，范围是 `0` 到 `4095`，参考电压按 `3300 mV` 计算。

## 建议演示流程

1. 运行 `python mcu_simulator.py`。
2. 用浏览器打开 `instrument_panel.html`。
3. 点击 `连接模拟器`。
4. 在信号发生器区域选择波形并调节频率、幅度、偏置。
5. 在示波器区域点击 `运行`，观察屏幕波形。
6. 调节 `Time/Div`、`Volt/Div`、位置、触发电平，模拟真实示波器操作。
7. 如果要演示真实硬件，则烧录 Keil 工程，接好 `PA9/PA10` 串口线，用 Chrome 或 Edge 点击 `连接 DAPmini 串口`。


