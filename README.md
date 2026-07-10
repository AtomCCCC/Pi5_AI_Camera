# Pi5 AI Camera

树莓派 5 本地 AI 助手机器人项目：语音交互 + 实时视觉 + 舵机控制 + 屏幕反馈。

## 当前状态

- 状态：开发中（以可运行、可联调为优先）
- 主平台：Raspberry Pi 5 (8GB) + AI HAT+ 2 (Hailo-10H)
- 相机：Camera Module 3 (IMX708)
- 执行器：2 x AZDelivery Servo Motor
- 显示：5 inch 电容触摸屏
- 服务通信：MQTT（Mosquitto）

## 已确认的架构方向

- 在线 LLM：DeepSeek V4（云端）
- 视觉检测：YOLOv8m（Hailo-10H）
- 视觉理解：Hailo VLM Path A（NPU）
- GPIO 控制：Pi 5 内核 PWM 方案（适配 Debian 13）
- 系统组织：Voice / Vision / LLM Orchestrator / GPIO / Session Manager 五服务解耦

## 已废除或暂停方案

- 废除：将 Qwen 作为主路径的旧说明与旧部署叙事
- 废除：根 README 中过长的代码级实现细节
- 暂停：Path B（`qwen2.5vl:3b` CPU 视觉理解）作为当前主线

说明：Qwen 相关内容仍可能保留在历史提交或子模块实验文件中，用于回溯，不作为主文档默认方案。

## 硬件配置（当前目标）

- 主控：Raspberry Pi 5 (8GB)
- AI 加速：AI HAT+ 2（Hailo-10H, 40 TOPS）
- 摄像头：Raspberry Pi Camera Module 3
- 舵机：AZDelivery Servo Motor x2
- 屏幕：5 inch 电容触摸屏 x1
- 音频：USB 麦克风 + 扬声器（按现场设备为准）
- 建议：独立 5V 舵机电源，舵机地线与 Pi 共地

## 系统模块

- Voice Service：唤醒词、语音识别、语音播报
- Vision Service：相机采集、实时检测、按需视觉理解
- LLM Orchestrator：对话推理、工具调用编排、结果回传
- GPIO Service：舵机、引脚控制、屏幕显示统一出口
- Session Manager：会话状态、上下文、打断控制

## MQTT 主题（简版）

- `command/in`：输入指令
- `response/out`：输出文本
- `vision/query` / `vision/result`：视觉理解请求与结果
- `vision/detect` / `vision/detect_result`：检测查询与结果
- `gpio/command`：硬件执行命令
- `session/interrupt`：会话打断

## 文档结构（建议阅读顺序）

- 项目总览：`pi5_assistant/README.md`
- LLM 编排：`pi5_assistant/llm_orchestrator/README.md`
- 视觉服务：`pi5_assistant/vision_service/README.md`
- GPIO 服务：`pi5_assistant/gpio_service/README.md`
- 语音服务：`pi5_assistant/voice_service/README.md`
- 会话服务：`pi5_assistant/session_manager/README.md`

## 维护说明

- 根 README 仅保留架构与协作信息，不放大段实现代码
- 具体实现、接口细节、调试流程放在各子模块 README
- 若硬件变更（舵机型号、屏幕型号、接线）请先更新本文件“硬件配置”

## 下一步建议

- 完成 2 路 AZDelivery 舵机与语音命令联动验收
- 完成 5 inch 电容触摸屏 UI 最小可用页面
- 完成端到端回归：语音输入 -> LLM -> 视觉/硬件工具 -> 语音与屏幕反馈
