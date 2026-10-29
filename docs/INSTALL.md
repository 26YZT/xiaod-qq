# 安装与首次登录

## 1. 准备环境

维护者验收平台为 macOS Apple Silicon。完整唱歌建议至少16 GB内存、12 GB可用磁盘；没有本地GPU也可能运行，但速度显著下降。API和模型下载需要可访问对应服务的网络。

Mac 使用 Homebrew 的示例：

```bash
brew install uv ffmpeg node git
brew install --cask docker
```

打开 Docker Desktop，按它的提示完成首次设置并等待引擎就绪。Docker 的服务条款与商业订阅要求由 Docker 自身决定。

安装器不修改 Mac QQ 应用。QQ 桥接运行在独立 Linux 容器，AstrBot 和唱歌任务运行在本机，以使用 Apple GPU。

## 2. 安装固定版本

```bash
./xiaod doctor
./xiaod install --singing
```

基础安装 `./xiaod install` 只安装聊天和 MiMo 语音。唱歌使用固定的推理模型，不启动训练，也不安装 GPT-SoVITS。`install --singing` 会安装独立依赖、校验模型与参考干声；首次模型下载为数GB。

安装中断可以重试。安装器验证源码提交与下载哈希，不会为了继续运行而关闭TLS校验。运行中的本套服务需要先停止才能重新安装。

## 3. 只填写自己的账号

```bash
./xiaod init
```

- 托管QQ：作为小D登录的账号。建议与主人常用号分开。
- 主人QQ：允许低频主动联系和管理任务的账号。
- 群号：只在填入的目标群启用群聊配置。
- DeepSeek Key：用于聊天、上下文判断与搜索；需要账号具有对应接口能力。
- MiMo Key：用于 `mimo-v2.5-tts-voiceclone`；没有填写时可先用文字，语音不可用。

自己的密钥只保存在 `.local/` 和 `runtime/` 的本地配置，不要粘贴进 Issue，也不要手动上传这两个目录。

自动化部署可复制 `config.example.json` 到自行保管的本机文件，填写后运行：

```bash
./xiaod init --config /path/to/my-private-settings.json
```

初始化不会导入维护者记忆，也不会覆盖你已经在面板中修改的人格。重新配置连接信息前需停止本套服务。

## 4. 启动和扫码

```bash
./xiaod start
```

默认地址：AstrBot `http://127.0.0.1:6185`；NapCat `http://127.0.0.1:6099/webui`。先在 AstrBot 完成首次管理密码设置。

```bash
./xiaod qq-token
```

这个命令仅在本机显示 NapCat 管理令牌。用它登录 NapCat 页面，扫描二维码，在手机QQ中确认登录。

必须扫码托管号，而非主人号。不要同时打开同号的另一个电脑QQ；手机QQ可保留。同意好友与群邀请仍按QQ实际界面和权限处理。

## 5. 确认链路

先用主人号私聊小D发一句“你好”，再明确要求一条语音，最后到目标群测试。唱歌首次可用“唱一小段”降低等待成本。

```bash
./xiaod status
```

日志位于 `runtime/logs/astrbot.log`。日志可能包含聊天文本，分享故障信息前请脱敏。

## 6. 停止、备份与端口冲突

```bash
./xiaod stop
```

只停止本目录启动的AstrBot与本目录对应的Docker项目，不删除登录和记忆。备份时保管 `.local/` 与 `runtime/astrbot/data/`、`runtime/napcat/`；这些是私人数据，不能作为公共仓库内容。

若端口已占用，安装器不会结束占用它的程序。停止本套服务，在 `.local/settings.json` 中更改三个端口后再次 `./xiaod init --config .local/settings.json`。本项目的安装目录保持固定；移动运行目录前先停止并备份。

## 下载和代理

如果 PyPI 连接异常，可为当前终端指定一个自己信任的HTTPS镜像，例如：

```bash
export UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple
```

模型下载支持 Hugging Face 的标准 `HF_ENDPOINT` 环境变量。下载仍需通过本项目的哈希检查，不要用 `--trusted-host` 或关闭证书校验。

默认MiMo直连用于维护者已验证的网络。若你的MiMo只能通过代理访问，请在MiMo插件配置关闭 `tts_direct_connection`，不要为此全局关闭网络安全检查。
