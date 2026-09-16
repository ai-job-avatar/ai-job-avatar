# 一键部署提示词（复制整段发给 AI 助手）

把下面**整段提示词**复制，发给任何能操作你电脑的 AI 助手（WorkBuddy、Cursor、豆包等），
它就会带着你完成部署。

**使用前只需要改一处**：把方括号 `【】` 里的内容换成你自己的情况。
**千万不要把 API Key 写在提示词里** —— 让 AI 每一步停下来、你自己去网页上填。

---

```text
你是一位很耐心的部署工程师，请帮我把下面这个开源项目部署到线上。
我对技术细节不熟，请你用大白话解释每一步在做什么，并且每完成一步都明确告诉我
"现在轮到我做什么"，我做完回复你之后再继续下一步。

【项目信息】
项目名称：AI 求职数字分身（网页上和 AI 分身聊天，用我本人的声音回答）
代码位置：【填写路径，例如：我已经解压到 C:\Users\我的名字\Desktop\ai-job-avatar】
代码来源：【填写来源，例如：GitHub 仓库 xxx 下载的 ZIP】
项目结构：
  - server.py         后台程序（聊天接口 + 语音合成 + 提供网页）
  - static/index.html 网页界面
  - requirements.txt  依赖清单
  - Procfile          云平台启动命令
  - config.env.example 配置模板
启动命令：gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120

【最终目标】
把它变成一个公网网址，我发在简历和社交账号里；别人用手机或电脑点开就能跟我聊天、
听到我的声音；我的电脑关机之后它照样能用。

【我已经准备好的东西】
- GitHub 账号（已登录）
- Render 账号（已用 GitHub 登录）
- 豆包方舟聊天 API Key（我有，但我要自己填，不要让我发给你）
- 豆包语音复刻 API Key 和 Speaker ID（我有，我自己填）
- 我的声音已经训练完了，Speaker ID 是 S_ 开头的字符串

【请按下面的顺序执行，每步验证通过再进行下一步】

第 1 步 检查环境
  检查我的电脑有没有 Python 3.10 以上版本。没有就告诉我从哪里下载、安装时要注意什么
  （提醒我勾选 Add Python to PATH）。

第 2 步 安装依赖
  在项目目录执行 pip install -r requirements.txt。如果下载慢，换清华镜像源。

第 3 步 配置密钥（这一条最重要，请严格遵守）
  帮我把 config.env.example 复制成 config.env，然后把这 5 个配置项的名字列给我，
  让我自己去填值：
    DOUBAO_API_KEY / DOUBAO_MODEL / TTS_API_KEY / TTS_SPEAKER_ID / TTS_RESOURCE_ID
  并且告诉我：这个文件已经被 .gitignore 排除了，不会被上传。
  不要要求我把 Key 发到对话里，也不要替我把 Key 写进文件。

第 4 步 本地启动并自检
  启动 server.py，然后访问 http://127.0.0.1:5000/api/health，确认返回里
  tts_engine 是 doubao_clone、audio_dir_writable 是 true。
  如果有问题，按这两条排查：
    - tts_engine 是 edge_tts_fallback → 音色相关的 Key 或 Speaker ID 没配好
    - audio_dir_writable 是 false → 语音目录建不出来
  排查时请先看启动时的命令行报错，再对照上面两条，不要让我盲目重试。

第 5 步 本地功能验收
  在浏览器打开首页，点一个快捷问题，确认三件事：
  ① 文字回答正常出现；② 有声音，而且是我的音色；③ 文字会跟着声音逐个高亮。
  然后开手机模拟（或真的用手机连同一个电脑）确认手机上也一样，且页面上没有播放按钮。

第 6 步 上传到 GitHub
  指导我新建一个仓库并上传代码。上传前你必须逐项检查：
    - 绝对没有 config.env
    - 绝对没有任何以 sk- / ark- / S_ 开头或 UUID 形式的密钥字符串
    - audio 目录要上传（里面要有 .gitkeep 占位文件）
  检查完之后把"我确认没有密钥"这句话明确告诉我。

第 7 步 部署到 Render
  一步步指导我：New + → Web Service → 选仓库 → Python 3 →
  Build Command 填 pip install -r requirements.txt →
  Start Command 用上面那行 gunicorn → Instance Type 选 Free。

第 8 步 配置环境变量
  把要添加的 5 个变量名和它们的作用列成表格给我，值我自己在网页上填。
  填完点 Create Web Service。

第 9 步 线上验收
  部署完成后访问 https://我的网址/api/health，确认和本地一致；
  再打开首页确认有声音。
  注意：Render 显示部署成功之后，新版有时候还要再等 20~30 秒才真正生效，
  如果第一次测不对，请等一等再测一次，不要急着改代码。

第 10 步 让链接随时打得开
  指导我注册 UptimeRobot（免费），添加一个 HTTP(s) 监控：
  地址填 我的网址/api/health，间隔 5 分钟。
  并解释为什么：Render 免费实例闲置 15 分钟会休眠，别人首次打开要等约 50 秒。

第 11 步 交付说明
  最后给我一份简单的说明，告诉我以后怎么改内容：
    - 改"分身说什么"：编辑 server.py 里 system_prompt 那一段文字
    - 改头像：替换 static/assets/th/avatar.webp（大图同名放 lg 目录）
    - 改卡片图片：替换 static/assets/th 与 lg 下的图片，并按需改 index.html 里的引用
    - 改完怎么发布：把文件重新上传到 GitHub，然后在 Render 点
      Manual Deploy → Deploy latest commit

【总体要求】
1. 任何时候都不要让 API Key 出现在聊天记录、代码文件或提交记录里。
2. 不要一次性把 11 步都讲给我，一步一步来，我确认完成了再进行下一步。
3. 报错时先让我把完整报错信息发给你，你判断原因；不要让我反复重试同一个操作。
4. 涉及"删除文件""重置配置"这类不可逆操作前，先告诉我影响，等我同意。
```

---

## 顺带说明

- 上面的提示词是"带我做"的版本，AI 负责判断和解释，你负责点鼠标、填 Key。
  这样最安全，也最容易学到东西。
- 如果你用的是能直接操作电脑的 AI（比如 WorkBuddy），它其实可以帮你把
  上传 GitHub、点 Render 部署这些动作都做完，你只需要在它需要授权时点一下确认。
- 部署完之后，记得每隔一段时间回来看一眼监控有没有报警。
