# Bailian Voice Clone MCP

一个可部署到阿里云 Function AI 的 `stdio` MCP，用于：

- 创建声音克隆
- 轮询音色状态
- 查询单个音色
- 列出音色
- 删除音色
- 用复刻音色做语音合成


## 本地启动

1. 安装依赖


## Path B：部署到阿里云 Function AI

### 1. 准备代码仓库

把这个目录推到 GitHub 或阿里云 Codeup：

- `server.py`
- `requirements.txt`
- `.env.example`
- `README.md`

### 2. 在 Function AI 创建 MCP 服务

1. 登录 Function AI 控制台
2. 创建空白项目
3. 新建服务，选择 `MCP 服务`
4. 传输类型选择 `SSE`
5. 开启鉴权
6. 运行环境选择 `Python`
7. 绑定你的代码仓库

### 3. 配置构建和启动

建议值：

- 构建命令：`pip install -t . -r requirements.txt`
- 启动命令：`python server.py`

资源建议：

- vCPU：1
- 内存：2 GB
- 弹性策略：`极速模式`
- 预置快照：`1`
- 实例上限：`1`

### 4. 配置环境变量

在 Function AI 的变量管理里新增：

- `DASHSCOPE_API_KEY`
- `DASHSCOPE_REGION=cn-beijing`
- `BAILIAN_TTS_MODEL=cosyvoice-v3.5-plus`
- `INLINE_AUDIO_BASE64_LIMIT=300000`

### 5. 部署并测试

部署成功后，Function AI 会给你一个公网 SSE 地址，通常是：

```text
https://xxxx.cn-beijing.fcapp.run/sse
```

先在 Function AI 控制台直接测试工具是否可用。

## 注册到百炼 MCP 管理

1. 打开百炼控制台 -> MCP 管理 -> 自定义服务
2. 点击 `+创建 MCP 服务`
3. 选择 `使用脚本部署`
4. 安装方式选 `http`
5. 填入你的 SSE 地址

配置示例：

```json
{
  "mcpServers": {
    "voice-clone-mcp": {
      "url": "https://xxxx.cn-beijing.fcapp.run/sse"
    }
  }
}
```

## 使用顺序建议

1. 调 `create_voice_clone`
2. 调 `wait_for_voice_ready`
3. 状态变成 `OK` 后，调 `synthesize_with_cloned_voice`

## 示例参数

### 创建声音克隆

```json
{
  "audio_url": "https://your-public-audio-url/sample.wav",
  "prefix": "myvoice01",
  "language_hint": "zh",
  "target_model": "cosyvoice-v3.5-plus",
  "region": "cn-beijing"
}
```

### 合成语音

```json
{
  "text": "你好，这是一段使用复刻音色生成的演示语音。",
  "voice_id": "cosyvoice-v3.5-plus-myvoice01-xxxxxxxx",
  "target_model": "cosyvoice-v3.5-plus",
  "region": "cn-beijing",
  "inline_base64": true
}
```

## 注意事项

- 声音克隆和声音合成用的 `target_model` 必须一致，否则合成会失败
- `audio_url` 必须公网可访问
- `prefix` 建议只用小写字母、数字、下划线，长度不超过 10
- `synthesize_with_cloned_voice` 默认会把音频落到临时目录；在云端想长期保存，下一步建议接 OSS

## Local Recording Support

The MCP now supports two additional tools for local recordings:

- `create_qwen_voice_clone_from_audio_base64`
- `create_qwen_voice_clone_from_local_file`

How to choose:

- If you deploy the MCP to Function AI / Bailian, use `create_qwen_voice_clone_from_audio_base64`.
  This is the remote-friendly path because you can pass audio as base64 or a full Data URL.
- If you run the MCP locally with `stdio`, use `create_qwen_voice_clone_from_local_file`.

Important:

- `CosyVoice` clone tools still require a public `audio_url`.
- Direct local-file clone support is implemented with `Qwen3 TTS VC`, because the official
  Qwen voice enrollment API supports `audio.data` while the CosyVoice clone API is documented
  around public URL input.

Example for remote base64 mode:

```json
{
  "audio_base64_or_data_url": "data:audio/wav;base64,AAA...",
  "preferred_name": "demo_voice_01",
  "audio_mime_type": "audio/wav",
  "target_model": "qwen3-tts-vc-2026-01-22",
  "region": "cn-beijing"
}
```

Example for local file mode:

```json
{
  "local_file_path": "C:\\Users\\29932\\Desktop\\sample.wav",
  "preferred_name": "demo_voice_01",
  "target_model": "qwen3-tts-vc-2026-01-22",
  "region": "cn-beijing"
}
```

## LobeHub HTTP Mode

LobeHub expects Streamable HTTP, not SSE.

This project now supports both transports:

- `MCP_TRANSPORT=stdio`
  For local stdio use or Function AI MCP proxy mode.
- `MCP_TRANSPORT=streamable-http`
  For direct LobeHub integration.

Recommended environment variables for direct LobeHub deployment:

```env
MCP_TRANSPORT=streamable-http
MCP_HOST=0.0.0.0
MCP_PORT=8080
```

Startup command for HTTP mode:

```bash
python server.py
```

LobeHub example config:

```json
{
  "mcpServers": {
    "voice-clone-mcp": {
      "url": "https://your-domain.example.com/",
      "url": "https://your-domain.example.com/mcp",
      "type": "streamable-http",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Important:

- For LobeHub, use the `/mcp` HTTP URL of the deployed service, not the old `/sse` URL.
- If you deploy this mode to Function AI, use a normal HTTP/Web service style deployment that exposes port `8080`.
