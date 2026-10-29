# 发布验证记录

验证日期：2026-10-29。发布目标：`26YZT/xiaod-qq`。

## 本机真实体验

维护者的 macOS Apple Silicon 部署已完成以下人工回测：

- QQ私聊回复与原生角色卡切换生效。
- 群聊不@、不点名：无明确对象的会员额度闲聊保持安静，角色玩笑能自然接梗，语气合适。
- L日常语音、歌声分段实际收到并正常播放。
- 自动搜索《茉莉花》、分离伴奏、按L方案2转换并混音；12秒试听的音色和伴奏均获确认，QQ缓存播放通过。

这些是维护者账号与设备上的体验证据，不代表所有QQ账号、网络或设备都已逐一验收。私人聊天与登录记录不随仓库发布。

## 公开包独立安装

在与运行中机器人分开的目录安装固定AstrBot提交和管理面板，使用自己的新Python包环境：

| 检查 | 结果 |
| --- | --- |
| AstrBot 4.29.3与面板SHA-256 | 通过 |
| Python 3.12基础服务和11个插件快照加载 | 通过，启动无错误 |
| 独立端口16185的HTTP服务 | 200 |
| 新数据库 | 仅初始角色与空群会话，没有导入维护者对话 |
| 群聊、群记忆、语音、点歌、可选动态签名控制 | 114项测试通过 |
| 安装配置、工具能力、账号绑定、素材校验与发布边界 | 6项测试通过 |
| 音源和混音逻辑 | 9项测试通过 |
| 独立Python 3.11歌声环境真实推理 | L方案2约12秒音频生成成功 |
| 独立伴奏环境 | BS-Roformer模型完整加载成功 |
| L参考音频SHA-256 | `7940eb2e9270ac58cd955e6271af217d405269f190bfd167e1489292afcd60af` |
| 发布树扫描 | 无服务凭据、私人数据库、日志或本机个人路径 |

模型和参考干声验证复用了本机已校验的缓存，以避免再次下载数GB文件；Python包环境没有共享旧项目的`site-packages`或GPT-SoVITS环境。首次安装下载地址另做了官方资源与音频响应核对。

测试实例没有启动第二个QQ桥接、没有用新号再扫码，也没有向真实QQ发送自动测试消息。安装者仍需用自己的API Key、账号和群完成首次回测。

## 当前边界

- 已验证设备路径为macOS Apple Silicon；Linux基础测试由CI提供，Linux/CUDA完整唱歌和Windows不作已验收承诺。
- MiMo、DeepSeek、QQ账号状态与音源站点访问受各自服务限制。依赖锁不能替代接口权限、余额和平台规则。
- 完整长音源首次制作等待较长；本仓库不承诺任意歌曲即时生成。
- QQ动态和签名的对外写入默认关闭，公开包没有再次执行真实发布。

## 复核命令

```bash
./xiaod install
cd runtime/astrbot
.venv/bin/python -m unittest discover -s ../../tests
.venv/bin/python -m unittest \
  data.plugins.astrbot_plugin_dafeiyu_chat.tests.test_chat \
  data.plugins.astrbot_plugin_dafeiyu_chat.tests.test_group_memory \
  data.plugins.astrbot_plugin_xiaod_voice.tests.test_voice \
  data.plugins.astrbot_plugin_xiaod_music.tests.test_music \
  data.plugins.astrbot_plugin_dafeiyu_presence.tests.test_presence
```

安装完整唱歌环境后，可在仓库根目录用 `runtime/song-lab/.separator-venv/bin/python -m unittest discover -s singing/tests` 复核音源边界与混音。
