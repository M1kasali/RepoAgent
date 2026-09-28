"""Pico 的 Internal Module Entry Point，支持执行 ``python -m pico``。

Python 以模块方式启动 Package 时会进入这里，再把控制权交给 `repoagent.harness.cli.commands.run`。该入口只负责
连接解释器与 CLI，不解析参数、不初始化 Agent Runtime，也不维护另一套命令实现；它应与安装后的
`pico` Console Script 表现一致。
"""

from repoagent.harness.cli.commands import run

if __name__ == "__main__":
    run()
