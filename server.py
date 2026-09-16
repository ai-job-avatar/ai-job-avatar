"""
数字分身后端服务器
- 代理豆包聊天API（保护API Key）
- TTS语音合成（支持豆包声音复刻 + edge-tts备用）
- 声音复刻训练（上传录音、查询状态）
- 静态文件服务
"""
import os
import json
import base64
import uuid
import time
import asyncio
import edge_tts
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import urllib.request

# ============ 密钥持久化 ============
# 以前密钥只存在于启动窗口的环境变量里，窗口一关就没了，
# 用"一键启动.bat"重启会变成没有密钥 -> 聊天报错 + 声音退回备用音色（不是本人声音）。
# 现在支持把密钥写在同目录的 config.env 里，重启自动读取。
def load_env_file(path='config.env'):
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # 已存在的环境变量优先，不覆盖
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    except Exception as e:
        print(f'[{path}] 读取失败: {e}')

load_env_file()

# ============ 支持把整份 config.env 塞进一个环境变量 ============
# 部署到 Render 这类平台时，逐个添加环境变量很麻烦，而且容易漏。
# 只要在平台上设置一个 CONFIG_ENV 变量，把 config.env 的内容整段粘进去，
# 这里会自动拆成对应的环境变量（已存在的环境变量仍然优先，不会被覆盖）。
def load_env_from_var(var_name='CONFIG_ENV'):
    raw = os.environ.get(var_name, '')
    if not raw:
        return
    try:
        for line in raw.replace('\\r\\n', '\n').replace('\\r', '\n').split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
        print(f'[{var_name}] 已从环境变量载入配置')
    except Exception as e:
        print(f'[{var_name}] 解析失败: {e}')

load_env_from_var()

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# ============ 静态资源缓存（这是"图片加载慢"的关键修复） ============
# 之前的现象：用电脑打开，证书图半天不出来；手机打开反而正常。
# 根因是图片本身既没压缩、也完全没被缓存住 —— 每次打开都重新往 Render 拉 7MB。
# 这里分成两档处理：
#   首页    → 永不缓存（改了马上生效，避免看到旧版本）
#   图片等  → 交给浏览器缓存一年（靠换文件名 / 加 ?v= 来更新）
# 注意：Flask 对静态文件默认发 no-cache，会强制每次回源校验，
# 所以这里必须"覆盖"而不能"补默认值"。
_CACHEABLE_EXT = (
    '.webp', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.bmp',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.css', '.js', '.mp3', '.m4a', '.wav', '.pdf',
)
_ONE_YEAR = 31536000


@app.after_request
def add_cache_headers(resp):
    try:
        path = (request.path or '').lower()
        # 首页、接口、动态音频各有自己的策略，一律不动
        if path in ('', '/') or path.endswith('.html') or path.startswith('/api/') or path.startswith('/audio/'):
            return resp
        if path.endswith(_CACHEABLE_EXT):
            resp.headers['Cache-Control'] = f'public, max-age={_ONE_YEAR}'
    except Exception as e:
        print(f'[cache] 设置缓存头失败（已忽略，不影响访问）: {e}')
    return resp

# ============ 运行期目录（关键修复） ============
# GitHub 不跟踪空目录，代码克隆到 Render 之后 audio/ 根本不存在；
# 而以前只在 `if __name__ == '__main__'` 里才 mkdir，
# 用 gunicorn 起服务时那段代码永远不执行 → /api/tts 写文件时
# FileNotFoundError: 'audio/xxx.mp3' → 前端拿不到音频 → "没有声音"。
# 改成模块导入时就建目录，本地 / gunicorn / Render 三种启动方式全覆盖。
# 同时把路径固定成"项目目录下的 audio"，不受启动时工作目录影响。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, 'audio')

def ensure_audio_dir():
    try:
        os.makedirs(AUDIO_DIR, exist_ok=True)
        return True
    except Exception as e:
        print(f'[audio] 创建音频目录失败: {e}')
        return False

ensure_audio_dir()

# 内存音频缓存：磁盘写不进去时的兜底（key 是文件名，value 是 mp3 字节）
MEM_AUDIO = {}

# ============ 配置 ============
CONFIG = {
    # 豆包聊天API（方舟）
    'doubao_api_key': os.environ.get('DOUBAO_API_KEY', ''),
    'doubao_model': os.environ.get('DOUBAO_MODEL', 'doubao-seed-1-6-251015'),
    'doubao_base_url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
    
    # 豆包语音合成（声音复刻V3 API）
    'tts_api_key': os.environ.get('TTS_API_KEY', ''),
    'tts_speaker_id': os.environ.get('TTS_SPEAKER_ID', ''),
    'tts_resource_id': os.environ.get('TTS_RESOURCE_ID', 'seed-icl-2.0'),
    'tts_base_url': 'https://openspeech.bytedance.com/api/v3/tts/unidirectional',
    
    # 备用TTS（edge-tts）
    'fallback_voice': 'zh-CN-YunxiNeural',
    
    # 系统提示词
    'system_prompt': '''你是林小舟的数字分身，基于真实简历信息回答问题。第一人称，口语化，真诚自然，不要太官方。只用提供的真实信息回答，不知道就说不清楚。回答简洁：默认 1-2 句话、40 字以内，只有对方明确追问细节时才展开。

【说明】下面是一份虚构的示例资料，请把它替换成你自己的真实信息（格式照抄即可）。

## 基本信息
林小舟，13800000000，xiaozhou@example.com，2027年毕业，云溪大学计算机科学与技术硕士，研究方向数据分析与自然语言处理。

## 教育背景
云溪大学 计算机科学与技术 硕士（2024.09-2027.07），本科也是云溪大学 软件工程（2020.09-2024.07）。硕士阶段连续两年获得学业奖学金。CET-6 已通过。

## 科研项目（两个项目我都是项目负责人）
项目一（用户评论情感分析，会议论文在审，第二作者，项目负责人）：中文评论里口语化表达多、情感倾向难判断，通用模型效果不稳。我负责需求拆解与方案设计，把任务拆成数据清洗、特征构建、模型对比三条线，在三个自建数据集上完成对照实验，主要指标提升约 3 个百分点。
项目二（轻量文本分类模型，期刊论文在投，第二作者，项目负责人）：在算力有限的环境下平衡效果与速度。设计了两级特征压缩与自适应融合结构，模型体量控制在 2.8M 参数左右，完成跨领域迁移测试。

## 实习经历
1. 星澜科技 数据分析实习生：负责用户行为数据的清洗与看板维护，梳理出 3 类高频流失场景并输出优化建议，协助把某功能的使用率提升了约 12%。
2. 恒信数据 运营助理实习生：负责活动数据复盘与用户反馈归集，独立完成每周数据周报，对接 2 个业务团队的取数需求。

## 获奖成果
硕士阶段连续两年获得学业奖学金。2025 年获校级数据分析竞赛优秀奖（项目负责人）。参与校级科研项目 2 项，申请软件著作权 1 项。

## 技能特长
数据分析：熟练使用 Excel 数据透视表与 Python 做数据清洗、统计分析和可视化，能独立搭建数据看板，会写 SQL 做多表查询。
AI 工具应用：长期使用 ChatGPT、Gemini、Cursor 等工具辅助资料调研、方案设计和脚本编写，能把模糊需求拆成明确指令并验证结果。
沟通与文档：有对接业务方取数需求的经验，能把数据结论讲成业务同事听得懂的话；具备调研报告、实验记录和汇报材料撰写能力。
协作与执行：做过项目负责人，熟悉任务拆解、进度跟踪和跨角色协调，做事细致、抗压能力好。

## 求职意向
主要方向：数据分析、产品、运营；也愿意做技术支持、管培生这类能快速熟悉业务的岗位。城市不限，可接受外派。踏实能抗压，学习能力强，愿意从基层做起。'''
}

# 对话历史
conversations = {}

# ============ 安全防护 ============
# 1. 速率限制：每个IP每分钟最多30次API请求
rate_limit = {}  # {ip: [timestamp1, timestamp2, ...]}
RATE_LIMIT_PER_MINUTE = 30

def check_rate_limit(ip):
    """检查IP是否超过速率限制，返回True表示允许"""
    now = time.time()
    if ip not in rate_limit:
        rate_limit[ip] = []
    # 清理1分钟前的记录
    rate_limit[ip] = [t for t in rate_limit[ip] if now - t < 60]
    if len(rate_limit[ip]) >= RATE_LIMIT_PER_MINUTE:
        return False
    rate_limit[ip].append(now)
    return True

# 2. 访问密码（可选）：在config.env中设置ACCESS_PASSWORD=你的密码
# 设置后，聊天和TTS接口需要在请求头中携带 X-Access-Password
ACCESS_PASSWORD = os.environ.get('ACCESS_PASSWORD', '')

def verify_access_password():
    """验证访问密码，返回True表示通过"""
    if not ACCESS_PASSWORD:
        return True  # 未设置密码则不限制
    provided = request.headers.get('X-Access-Password', '')
    return provided == ACCESS_PASSWORD

def get_client_ip():
    """获取客户端真实IP（考虑反向代理）"""
    return request.headers.get('CF-Connecting-IP', 
           request.headers.get('X-Forwarded-For', 
           request.remote_addr or 'unknown'))

# ============ 静态文件 ============
@app.route('/')
def index():
    # 不允许浏览器/中间层缓存首页，避免改了前端手机上还看到旧版本
    resp = send_from_directory('static', 'index.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# ============ 聊天API ============
@app.route('/api/chat', methods=['POST'])
def chat():
    # 安全检查：速率限制
    ip = get_client_ip()
    if not check_rate_limit(ip):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    # 安全检查：访问密码
    if not verify_access_password():
        return jsonify({'error': '访问密码错误'}), 403
    
    data = request.json
    question = data.get('question', '')
    session_id = data.get('session_id', 'default')
    
    if not question:
        return jsonify({'error': '问题不能为空'}), 400
    
    if session_id not in conversations:
        conversations[session_id] = []
    conversations[session_id].append({'role': 'user', 'content': question})
    history = conversations[session_id][-12:]
    
    messages = [{'role': 'system', 'content': CONFIG['system_prompt']}] + history
    
    payload = {
        'model': CONFIG['doubao_model'],
        'messages': messages,
        'temperature': 0.7,
        'max_tokens': 500
    }
    
    req = urllib.request.Request(
        CONFIG['doubao_base_url'],
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {CONFIG["doubao_api_key"]}'
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            answer = result['choices'][0]['message']['content']
            conversations[session_id].append({'role': 'assistant', 'content': answer})
            return jsonify({'answer': answer})
    except Exception as e:
        return jsonify({'error': f'API调用失败: {str(e)}'}), 500

# ============ TTS语音合成 ============
@app.route('/api/tts', methods=['POST'])
def tts():
    # 安全检查：速率限制
    ip = get_client_ip()
    if not check_rate_limit(ip):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    # 安全检查：访问密码
    if not verify_access_password():
        return jsonify({'error': '访问密码错误'}), 403
    
    data = request.json
    text = data.get('text', '')
    if not text:
        return jsonify({'error': '文本不能为空'}), 400
    
    import hashlib
    # 关键：克隆音色和备用音色分文件缓存。
    # 如果共用同一个文件名，一旦某次豆包复刻失败走了 edge-tts，
    # 那段"不是本人声音"的音频就会被永久缓存住，之后一直播错音色。
    digest = hashlib.md5(text.encode()).hexdigest()
    clone_name = digest + '.mp3'
    edge_name = 'edge_' + digest + '.mp3'
    clone_path = os.path.join(AUDIO_DIR, clone_name)
    edge_path = os.path.join(AUDIO_DIR, edge_name)

    # 兜底：磁盘目录万一被清掉（Render 重启会重置文件系统），这里再建一次
    ensure_audio_dir()

    clone_ready = bool(CONFIG['tts_api_key'] and CONFIG['tts_speaker_id'])

    if os.path.exists(clone_path):
        return jsonify({'audio_url': f'/audio/{clone_name}', 'engine': 'doubao_clone'})

    # 优先用豆包声音复刻V3
    if clone_ready:
        try:
            audio_data = doubao_tts_v3(text)
            with open(clone_path, 'wb') as f:
                f.write(audio_data)
            return jsonify({'audio_url': f'/audio/{clone_name}', 'engine': 'doubao_clone'})
        except Exception as e:
            print(f'豆包TTS失败，使用备用: {e}')

    # 备用：edge-tts
    # 注意：这个通道会偶发返回空音频（网络抖动 / 被限流），表现为 500 且前端
    # 一直停在"声音生成中"。所以这里改成失败重试，而不是一次失败就整段哑掉。
    try:
        if not os.path.exists(edge_path) and edge_name not in MEM_AUDIO:
            def synth_to_file(path):
                async def gen():
                    communicate = edge_tts.Communicate(text, CONFIG['fallback_voice'])
                    await communicate.save(path)
                    return None
                return asyncio.run(gen())

            def synth_to_mem():
                async def gen():
                    communicate = edge_tts.Communicate(text, CONFIG['fallback_voice'])
                    buf = bytearray()
                    async for chunk in communicate.stream():
                        if chunk['type'] == 'audio':
                            buf.extend(chunk['data'])
                    return bytes(buf)
                return asyncio.run(gen())

            saved = False
            for attempt in range(1, 4):
                try:
                    synth_to_file(edge_path)
                    if os.path.exists(edge_path) and os.path.getsize(edge_path) > 0:
                        saved = True
                        break
                    raise RuntimeError('返回的音频是空的')
                except Exception as gen_err:
                    print(f'[audio] 第 {attempt} 次合成失败: {gen_err}')
                    if attempt < 3:
                        time.sleep(1.2)

            if not saved:
                # 可能是磁盘不可写（容器只读 / 目录被清空），也可能重试仍失败。
                # 改用内存缓存直接下发，宁可退化成备用音色，也不要整段哑掉。
                print('[audio] 改用内存缓存下发')
                try:
                    MEM_AUDIO[edge_name] = synth_to_mem()
                except Exception as mem_err:
                    print(f'[audio] 内存合成也失败: {mem_err}')
                    return jsonify({'error': f'TTS生成失败: {str(mem_err)}'}), 500
        return jsonify({'audio_url': f'/audio/{edge_name}', 'engine': 'edge_tts'})
    except Exception as e:
        return jsonify({'error': f'TTS生成失败: {str(e)}'}), 500

def doubao_tts_v3(text):
    """调用豆包声音复刻V3 API合成语音"""
    request_id = str(uuid.uuid4())
    payload = {
        'req_params': {
            'text': text,
            'speaker': CONFIG['tts_speaker_id'],
            'audio_params': {
                'format': 'mp3',
                'sample_rate': 24000
            }
        }
    }
    
    req = urllib.request.Request(
        CONFIG['tts_base_url'],
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-Api-Key': CONFIG['tts_api_key'],
            'X-Api-Resource-Id': CONFIG['tts_resource_id'],
            'X-Api-Request-Id': request_id,
        }
    )
    
    audio_data = b''
    with urllib.request.urlopen(req, timeout=30) as resp:
        # 流式响应，逐行解析
        for line in resp:
            line_str = line.decode('utf-8').strip()
            if not line_str:
                continue
            if line_str.startswith('data:'):
                json_str = line_str[5:].strip()
            else:
                json_str = line_str
            if not json_str:
                continue
            try:
                chunk = json.loads(json_str)
                # code=0或20000000都表示成功
                if chunk.get('code') not in (0, None, 20000000):
                    raise Exception(f"豆包TTS错误: code={chunk.get('code')}, msg={chunk.get('message')}")
                if 'data' in chunk and chunk['data']:
                    audio_data += base64.b64decode(chunk['data'])
            except json.JSONDecodeError:
                continue
    
    if not audio_data:
        raise Exception("豆包TTS未返回音频数据")
    
    return audio_data

# ============ 声音复刻训练 ============
# 说明：当前版本的声音复刻已经在豆包控制台完成并拿到 Speaker ID，
# 所以下面两个接口不是必需（且旧代码引用了不存在的 CONFIG 键会直接 500）。
# 这里改为安全取值，未配置时返回明确提示，而不是抛 500。
def _clone_train_ready():
    return all(CONFIG.get(k) for k in ('tts_appid', 'tts_access_token', 'tts_speaker_id'))

@app.route('/api/voice-clone/upload', methods=['POST'])
def voice_clone_upload():
    """上传录音训练复刻音色（需配置 TTS_APPID / TTS_ACCESS_TOKEN / TTS_SPEAKER_ID）"""
    # 安全检查：此接口必须密码保护，防止别人篡改你的声音
    if not ACCESS_PASSWORD:
        return jsonify({'error': '声音克隆接口已禁用：请在config.env中设置ACCESS_PASSWORD后启用'}), 403
    if not verify_access_password():
        return jsonify({'error': '访问密码错误'}), 403
    # 速率限制
    ip = get_client_ip()
    if not check_rate_limit(ip):
        return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
    
    if not _clone_train_ready():
        return jsonify({
            'error': '声音复刻训练未启用：当前作品已直接使用豆包控制台训练好的 Speaker ID，无需重新上传录音。'
        }), 400

    audio_path = (request.json or {}).get('audio_path', '')
    if not audio_path or not os.path.exists(audio_path):
        return jsonify({'error': '音频文件不存在'}), 400

    with open(audio_path, 'rb') as f:
        audio_bytes = base64.b64encode(f.read()).decode()

    payload = {
        'appid': CONFIG['tts_appid'],
        'speaker_id': CONFIG['tts_speaker_id'],
        'audios': [{
            'audio_bytes': audio_bytes,
            'audio_format': 'm4a'
        }],
        'source': 2,
        'language': 0,  # 中文
        'model_type': 4,  # ICL2.0
        'extra_params': json.dumps({
            'enable_audio_denoise': True,
            'enable_crop_by_asr': True
        })
    }

    req = urllib.request.Request(
        CONFIG['tts_upload_url'],
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer;{CONFIG["tts_access_token"]}',
            'Resource-Id': 'seed-icl-2.0'
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        if result.get('BaseResp', {}).get('StatusCode') != 0:
            return jsonify({'error': result['BaseResp'].get('StatusMessage', '上传失败')}), 500
        return jsonify({'status': 'uploaded', 'speaker_id': result.get('speaker_id')})
    except Exception as e:
        return jsonify({'error': f'上传失败: {str(e)}'}), 500

@app.route('/api/voice-clone/status', methods=['GET'])
def voice_clone_status():
    """查询音色训练状态"""
    if not _clone_train_ready():
        return jsonify({
            'error': '声音复刻训练未启用：当前作品已直接使用豆包控制台训练好的 Speaker ID。'
        }), 400

    payload = {
        'appid': CONFIG['tts_appid'],
        'speaker_id': CONFIG['tts_speaker_id']
    }

    req = urllib.request.Request(
        CONFIG['tts_status_url'],
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer;{CONFIG["tts_access_token"]}',
            'Resource-Id': 'seed-icl-2.0'
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        status_map = {0: 'NotFound', 1: 'Training', 2: 'Success', 3: 'Failed', 4: 'Active'}
        return jsonify({
            'status': status_map.get(result.get('status'), 'Unknown'),
            'status_code': result.get('status'),
            'speaker_id': result.get('speaker_id'),
            'demo_audio': result.get('demo_audio', '')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ 音频文件服务 ============
@app.route('/audio/<path:filename>')
def serve_audio(filename):
    # 优先读内存缓存（磁盘不可写时的兜底），再回落磁盘
    cached = MEM_AUDIO.get(filename)
    if cached:
        from flask import Response
        resp = Response(cached, mimetype='audio/mpeg')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
    return send_from_directory(AUDIO_DIR, filename)

# ============ 健康检查 ============
# 部署到云端后，用它一眼看出"聊天密钥/克隆音色的密钥到底有没有配上"，
# 不用再靠猜。（之前没有这个接口，排查只能抓瞎。）
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'audio_dir': AUDIO_DIR,
        'audio_dir_writable': os.access(AUDIO_DIR, os.W_OK),
        'chat_api_configured': bool(CONFIG['doubao_api_key']),
        'tts_clone_configured': bool(CONFIG['tts_api_key'] and CONFIG['tts_speaker_id']),
        'tts_engine': 'doubao_clone' if (CONFIG['tts_api_key'] and CONFIG['tts_speaker_id']) else 'edge_tts_fallback',
        'mem_audio_count': len(MEM_AUDIO),
    })

# ============ 配置查询 ============
@app.route('/api/config', methods=['GET'])
def get_config():
    # 注意：tts_appid / tts_access_token 这两个键在 CONFIG 里根本不存在，
    # 以前直接 CONFIG['tts_appid'] 会抛 KeyError → 这个接口线上一直 500。
    # 换成 .get() 取值，缺失就当作未配置。
    return jsonify({
        'chat_api_configured': bool(CONFIG['doubao_api_key']),
        'voice_clone_configured': bool(
            CONFIG.get('tts_appid') and CONFIG.get('tts_access_token') and CONFIG.get('tts_speaker_id')
        ),
        'tts_engine': 'doubao_clone' if (CONFIG['tts_api_key'] and CONFIG['tts_speaker_id']) else 'edge_tts_fallback'
    })

if __name__ == '__main__':
    ensure_audio_dir()
    print('=' * 60)
    print('  数字分身 - 后端服务器已启动')
    print('=' * 60)
    print(f'  聊天API: {"已配置" if CONFIG["doubao_api_key"] else "未配置"}')
    print(f'  声音复刻: {"已配置" if (CONFIG["tts_api_key"] and CONFIG["tts_speaker_id"]) else "未配置（使用edge-tts备用）"}')
    print(f'  音频目录: {AUDIO_DIR}')
    print(f'  访问地址: http://localhost:5000')
    print('=' * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)