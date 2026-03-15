为了完美替代 `get_weather` 和 `web_fetch`，保持它们作为“轻量级 I/O 密集型工具”的特性，我强烈建议你在本地 Python MCP 服务端写两个纯本地的 Mock 工具。

### 替代方案：本地 Sleep 挡板工具 (Mock I/O Tools)

这两个 Mock 工具的核心思想是：**不发真实的网络请求，而是通过 `asyncio.sleep()` 或 `time.sleep()` 模拟出一段固定的“网络往返延迟（RTT）”，然后返回一段伪造的静态数据。**

你可以直接在你的 `mcp_server/server/tools/` 目录下创建这两个文件：

#### 1. 替代 `get_weather` -> `mock_weather.py`

模拟一个耗时约为 50 毫秒的轻量级 API 查询。

```python
import asyncio
import json

async def mock_get_weather(location: str) -> str:
    """
    Mock 工具：模拟查询天气的轻量级 I/O 请求
    """
    # 模拟真实调用外部天气 API 的网络延迟 (例如 50ms)
    # 使用 asyncio.sleep 模拟非阻塞 I/O，不会卡死后端的其他并发请求
    await asyncio.sleep(0.05) 
    
    # 返回固定的伪造数据
    fake_data = {
        "location": location,
        "temperature": "25°C",
        "condition": "Sunny",
        "source": "Mock API (No Internet Required)"
    }
    return json.dumps(fake_data)

```

#### 2. 替代 `web_fetch` -> `mock_web_fetch.py`

模拟一个耗时约为 150 毫秒，且返回数据量稍大一点的网页拉取请求。

```python
import asyncio

async def mock_web_fetch(url: str) -> str:
    """
    Mock 工具：模拟拉取网页的 I/O 请求
    """
    # 网页抓取通常比天气 API 慢，模拟 150ms 延迟
    await asyncio.sleep(0.15) 
    
    # 返回一段伪造的 HTML 或长文本
    fake_html = f"""
    <html>
        <head><title>Mock Page for {url}</title></head>
        <body>
            <h1>This is a locally mocked response.</h1>
            <p>It simulates fetching data from the external network without actual IP bans.</p>
            <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit...</p>
        </body>
    </html>
    """
    return fake_html

```

### 为什么这种 Mock 方案对发论文极其有利？

1. **绝对的确定性 (Determinism)**：
外网的延迟是波动的（一会儿 20ms，一会儿 500ms），这会让你的 P95/P99 延迟曲线出现无法解释的毛刺。使用了 Mock 工具后，底层基准延迟被严格固定在了 50ms 和 150ms。这样一来，压测时产生的**任何额外延迟，都100%是你网关排队或处理造成的**，实验数据极其干净、严谨。
2. **无限并发能力 (Infinite Scalability)**：
你现在可以放心大胆地把压测脚本的 RPS（每秒请求数）调到 1000 甚至 5000。只要你的本地机器 CPU 撑得住，再也不会有“被封 IP”或“HTTP 429”的报错来干扰你的吞吐量测试了。
3. **完美充当“老鼠流”**：
在结合我们之前讨论的 `heavy_ratio`（异构混合负载）时，这两个 Mock 工具作为不消耗 CPU、不消耗显存的“轻量级老鼠流”，可以完美验证你的网关在重载大模型请求（大象流）把系统搞崩溃的边缘，依然能保障这些轻量请求的顺畅通行。

**总结：**
赶紧把真实的 `get_weather` 和 `web_fetch` 从压测配置里摘掉。换上这两个 `mock_` 开头的工具，你的本地压测就可以像在完全隔离的无菌实验室里一样，跑出最完美的基线数据了！