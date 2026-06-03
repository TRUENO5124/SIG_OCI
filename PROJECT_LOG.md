# 工程日志

## 目标

基于 STM32F103C8T6 做一个简易“信号发生器 + 示波器”一体项目。

老师要求最终交付 3 项内容：

- 可以直接用 Keil 打开、编译、下载的单片机工程。
- 上位机软件。
- 下位机单片机模拟器。

约束条件：

- 使用现有 STM32 开发板。
- 不额外增加电路。
- 网络端口号使用 `7897`。
- 单片机为 48 引脚 STM32F103C8T6。
- 烧录器改为 DAPmini；DAPmini 同时带 `TX/RX` 串口引脚，可直接和单片机串口通信。

## 硬件选择

当前引脚分配：

- `PA9` / USART1_TX：单片机串口发送，接 DAPmini 的 RX。
- `PA10` / USART1_RX：单片机串口接收，接 DAPmini 的 TX。
- `PA6` / TIM3_CH1 PWM：信号发生器输出。
- `PA0` / ADC1_IN0：示波器输入。
- `PC13`：常见 Blue Pill 板载 LED，用作心跳/状态灯。

DAPmini 下载调试使用 STM32 默认 SWD 引脚：

- `PA13` / SWDIO：接 DAPmini SWDIO。
- `PA14` / SWCLK：接 DAPmini SWCLK。
- `GND`：必须共地。
- `3.3V` / VTref：给 DAPmini 提供目标板参考电平，是否由 DAPmini 给板子供电取决于实际模块。
- `NRST`：可接可不接，接上后下载复位更稳定。

重要限制：

- STM32F103C8T6 没有片上 DAC，所以信号源使用定时器 PWM 实现。
- 在不增加外部模拟滤波电路的情况下，正弦波、三角波、锯齿波本质上是 PWM 占空比调制；方波可以直接由 PWM 输出。
- 硬件自测时可用一根杜邦线把 `PA6` 接到 `PA0`，让单片机采集自己输出的信号。
- 如果不方便接真实硬件，可用 `mcu_simulator.py` 演示完整流程。

## 当前状态

- `main.c` 已实现：
  - USART1 文本协议。
  - `PA6` 上的 TIM3_CH1 PWM 信号源。
  - TIM2 40 kHz 波形更新中断。
  - `PA0` 上的 ADC1_IN0 示波采样。
  - 命令：`PING`、`INFO`、`GEN`、`CAP`、`HELP`。
- `mcu_simulator.py` 已实现：
  - 监听 `127.0.0.1:7897`。
  - 同一端口同时支持普通 TCP 文本协议和 WebSocket。
  - 协议与真实固件一致。
  - 可生成模拟 12 位 ADC 采样数据。
- `instrument_panel.html` 已实现：
  - HTML 示波器和信号发生器一体面板。
  - 不需要手动输入底层命令，使用按钮、旋钮、开关和屏幕操作。
  - 可修改参数均支持直接在数值框中手动输入。
  - `Time/Div` 最小值为 `1 ms/div`。
  - 采样解析会按 `DATA` 头声明的点数保留数据，多余采样点会丢弃，避免 `543/512` 这类旧帧混入错误。
  - 采样等待时间会根据采样点数和采样率动态计算；采集超时后自动停止运行模式，避免连续刷屏。
  - 通过 `ws://127.0.0.1:7897` 连接模拟器。
  - 通过浏览器 Web Serial 以 115200 波特率连接 DAPmini 串口。
- 自动启动脚本已实现：
  - `run_instrument_panel.bat`：Windows 双击入口。
  - `run_instrument_panel.ps1`：自动启动/复用 `mcu_simulator.py`，打开 `instrument_panel.html`。
  - 默认使用端口 `7897`；如果端口已经被占用且可连接，则直接复用已有模拟器。
- `host_app.py` 已实现：
  - Tkinter 界面。
  - 支持 TCP 模拟器模式。
  - 安装 `pyserial` 后支持串口真实开发板模式。
  - 包含示波器画布和协议日志。
- `README.md` 已中文化：
  - 说明 HTML 面板为推荐上位机。
  - 说明 DAPmini 的 SWD 下载调试和 `TX/RX` 串口通信。
  - 说明 `PA9/PA10` 的作用。
- Keil 工程 XML 已更新：
  - 定义芯片宏 `STM32F10X_MD`。
  - 头文件路径为 `.\Start`。
  - 工程组精简为 `Application` 和 `Startup`。
  - 编译单元为 `main.c`、`Start/system_stm32f10x.c`、`Start/startup_stm32f10x_md.s`。

## 已验证

- `python -m py_compile host_app.py mcu_simulator.py` 通过。
- `run_instrument_panel.ps1` PowerShell 语法检查通过。
- TCP 模拟器冒烟测试通过：
  - `PING`
  - `INFO`
  - `GEN TRIANGLE 500 80 50`
  - `CAP 16 2000`
- WebSocket 模拟器冒烟测试通过：
  - WebSocket 握手返回 `HTTP/1.1 101 Switching Protocols`。
  - `GEN SINE 1000 70 50`
  - `CAP 8 1000`
- `instrument_panel.html` 静态结构检查通过：
  - 必需的 canvas、连接按钮、波形按钮、旋钮和日志元素存在。
- Keil 命令行构建通过。

## 回退记录

- 2026-06-04：已按用户要求回退后续串口排查增强，保留 HTML 仪器面板、DAPmini 文档和自动启动脚本。
- 回退前文件已备份到 `backup_before_revert_20260604_023033`。

## 接续说明

如果之后上下文被压缩，从本文件继续即可。优先保持交付物可打开、可编译、可演示，而不是重新规划。
