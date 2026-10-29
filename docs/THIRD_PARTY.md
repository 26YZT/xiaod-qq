# 来源与许可

本仓库是可复现整合包，保留各组件作者与许可证。下表中的源码快照位于 `plugins/`，具体版本机器可读记录见 [`third_party/components.json`](../third_party/components.json)。

| 组件 | 来源 | 许可 |
| --- | --- | --- |
| AstrBot 4.29.3 | [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) | AGPL-3.0-or-later，安装时下载固定提交 |
| NapCat | [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ)、[napcat-docker](https://github.com/NapNeko/NapCat-Docker) | 遵循上游项目和固定容器发行许可，镜像不打包在仓库 |
| 上下文场景感知 | [muyouzhi6/astrbot_plugin_context_aware](https://github.com/muyouzhi6/astrbot_plugin_context_aware) | MIT，保留本机验证快照 |
| 认主 | [QuanWander/astrbot_plugin_master_aware](https://github.com/QuanWander/astrbot_plugin_master_aware) | MIT |
| 世界书 | [Zhalslar/astrbot_plugin_worldbook](https://github.com/Zhalslar/astrbot_plugin_worldbook) | GPL-3.0 |
| MiMo TTS | [Justice-ocr/astrbot_plugin_mimo_tts_clone](https://github.com/Justice-ocr/astrbot_plugin_mimo_tts_clone) | MIT，含经过验证的直连/超时与命令适配 |
| 表情包管理 | [anka-afk/astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) | MIT，保留关键时刻发送适配 |
| 情绪管理 | [middcom/astrbot_plugin_affection](https://github.com/middcom/astrbot_plugin_affection) | AGPL-3.0 |
| 私聊主动消息 | [Pancakes-Labs/astrbot_plugin_proactive_chat](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat) | AGPL-3.0 |
| Seed-VC | [Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc) | GPL-3.0，固定源码提交，附推理兼容补丁 |
| Audio Separator | [nomadkaraoke/python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) | MIT，固定0.30.2，只启用本包验证的BS-Roformer推理路径 |
| yt-dlp | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 采用上游对应许可，安装时下载 |

自定义的群聊节奏、语音节奏、自动点歌、动态签名及安装工具以本仓库AGPL-3.0-or-later许可发布。第三方目录的原许可证文本随源码保留，不将其改署为本项目原创。

表情管理页面附带的Font Awesome Free 6.4.0字体、图标和CSS分别遵守其OFL、CC BY和MIT条款，官方合并许可文本保存在 `plugins/meme_manager/pages/app/shared/webfonts/LICENSE.txt`。

## 模型与声音

模型权重不提交到Git；首次唱歌安装按 [`singing/models.lock.json`](../singing/models.lock.json) 下载固定修订并校验SHA-256。

- [Plachta/Seed-VC](https://huggingface.co/Plachta/Seed-VC)
- [openai/whisper-small](https://huggingface.co/openai/whisper-small)
- [nvidia/bigvgan_v2_44khz_128band_512x](https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x)
- [funasr/campplus](https://huggingface.co/funasr/campplus)
- [lj1995/VoiceConversionWebUI](https://huggingface.co/lj1995/VoiceConversionWebUI)

各权重遵守自己的上游许可，不能用代码AGPL标识替代模型授权。

参考干声和C音色的原始来源是 [尹紫鹭幻想社 / YinZilu 大肥鱼中文语音与演唱干声数据集](https://www.modelscope.cn/datasets/YinZilu/da-fei-yu-girl-voice-zh)，采用 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)。C为基于数据集克隆合成后保存的参考；其余F/G/H/I/L为维护者用MiMo文字设计生成并保存的参考，不是从真实人物录音训练出的角色模型。

本仓库不打包完成的歌曲缓存。可用音源的获取、转换和传播也必须符合相应歌曲与录音的授权，数据集许可不代表获得任意歌曲的商业授权。
