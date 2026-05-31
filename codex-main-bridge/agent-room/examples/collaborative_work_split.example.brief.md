# 协作任务：实现一个简单的Web服务

## 用户消息

有时候一个大任务的代码可能要写很多，这个时候就可以同时写不同部分的代码，最后互相审查，然后修改直到跑通

## 任务目标

通过一个演示任务，验证多Agent协作拆分生产的可行性：
- openclaw-main定义schema和API契约
- Codex实现数据库层
- Claude Code实现API层
- 互相审查代码

## 约束条件
- 所有代码必须放在 /home/lcxhh/.openclaw/workspace/codex-main-bridge/agent-room/examples/collab-demo/ 目录下
- 不能修改任何生产环境的代码
- 使用Python、FastAPI、SQLAlchemy
