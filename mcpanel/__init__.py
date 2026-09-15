"""MC Panel —— 零依赖的 Minecraft 服务端面板开服器。

模块划分：
    util        通用工具（编码、JSON、字节格式化）
    procstat    跨平台进程资源占用采集
    java        本机 Java 环境探测
    config      面板全局配置
    properties  server.properties 读写与字段元数据
    downloader  各服务端核心的下载 / 安装
    instance    单个服务端实例的进程管理
    web         内置 HTTP 面板服务
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
