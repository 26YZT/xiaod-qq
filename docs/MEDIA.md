# 展示素材制作记录

使用内置 imagegen 生成封面和欢迎表情；参考图仅用于鲸鱼娘形象，不使用真人头像。图片已保存到 `assets/banner.png` 与 `assets/welcome.png`。

视频来自维护者提供的22.917秒录屏，使用FFmpeg转为H.264/AAC；对聊天区右侧真人头像列模糊处理，移除原元数据，保留真实聊天内容和演示声音。GIF与海报从处理后的视频提取，没有伪造聊天界面或回复。

## 配图提示词

### releaseBanner

```text
Create a polished wide GitHub README illustrated banner, approximately 3:1 aspect ratio, 1536 by 512. Use the attached image ONLY as a visual reference for the little chibi blue whale girl illustrated in the lower chat sticker: wavy deep-blue hair fading to sky blue, big blue eyes, white maid headband, modest navy-and-white maid dress, rounded whale tail, clever cheerful slightly proud expression. Do not use any real human avatar, photograph, nickname, phone UI, or chat screenshot from the reference. This is an illustrated project cover for 小D, a QQ companion that chats, remembers context, sends occasional voice and stickers, and sings. Restrained pale ice-blue background with a few simple wave curves and small understated speech and music symbols. The whale girl on the right occupies about one third; elegant generous whitespace and large dark navy title on the left. Exact visible text, correctly spelled: big title “小D”; subtitle “鲸鱼娘的 QQ 日常”; small footer “Chat · Memory · Voice · Music”. Flat hand-drawn sticker-like illustration, crisp line art, no photorealism, no complicated dashboard, no badges, no extra characters, no brand logos, no confetti. No imitation screenshot, do not suggest this is an official DeepSeek product.
```

### releaseStarterMeme

```text
Create ONE square transparent-background QQ welcome reaction sticker, approximately 1024 by 1024. The reference image is ONLY character appearance inspiration: the small chibi blue whale girl in the lower chat sticker, with wavy deep-blue hair fading to sky blue, big blue eyes, white maid headband, modest navy-and-white maid dress, rounded whale tail. Do NOT use any real human face, avatar, username, phone UI, or chat text from the reference. Draw this cute whale girl waving hello with one hand, holding a small bowl of white rice with the other, cheerful and a little proud, compact expressive pose, bold clean navy outlines and white sticker border, tasteful blue-white palette. Exact large readable Chinese caption integrated above her head: “新朋友来啦！” This must be a single reusable sticker, not a sheet or collage. True transparent background with alpha, no solid backdrop, no other characters, no watermarks, no existing brand logos.
```

