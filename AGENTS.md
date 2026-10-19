# qa-task-guide-1018d 开发说明

这是一个独立智能体项目。先阅读 README.md、agent.yaml、提示词和评测样例。
app.py 是协议入口与适配层；role.py 是岗位业务模块，execute(data, ctx) 执行业务逻辑。
辅助业务模块可以按需放在 tools/，不要求把 role.py 的逻辑搬到入口或 tools/。
行为改动同步维护 prompts/、tests/ 和可见评测，提交前运行项目测试。
平台运行时通过版本化 keel-sdk 引入，不把底座源码、契约或密钥复制进项目。
