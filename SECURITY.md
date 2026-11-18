# 安全问题反馈

这是一个本地小工具，代码全部在本仓库内，没有服务端。

## 报告方式

请到 [Issues](../../issues) 提交，标题写 `[安全]`，附：

1. 版本号（程序标题栏里）
2. 复现步骤
3. 期望行为 / 实际行为
4. 如果是崩溃或异常，附 `%LOCALAPPDATA%\DouyinAutoMessage\logs` 里对应的日志

## 已知情况

- 程序**未做数字签名**，Windows Defender 可能对 `Behavior:Win32/Execution.A!ml` 误报。
  这是「未签名 + 创建计划任务」组合触发的启发式判定，不是恶意行为；详见 README 的放行说明。
- 程序会创建 2 个计划任务（`DouyinAutoMessageMorning` / `DouyinAutoMessageEvening`），
  以当前用户、最低权限运行。
- 登录状态（cookie）存放在本机 `profile` 目录，未加密；请勿把该目录发给他人。

## 不在范围内

- 抖音平台自身的风控、限流、封号
- 用户自行修改代码后引入的问题


