import time
from openai import OpenAI

# 配置本地大模型的 API 地址
API_BASE = "http://localhost:9999/v1"
API_KEY = "EMPTY"  # 本地测试通常不需要 key
MODEL_NAME = "qwen" 

def test_local_llm():
    print(f"🔄 正在连接本地大模型服务: {API_BASE} ...")
    try:
        client = OpenAI(api_key=API_KEY, base_url=API_BASE)
        prompt = "请用一句话解释一下什么是云计算中的负载均衡？"
        print(f"📝 发送 Prompt: {prompt}\n")

        start_time = time.time()
        first_token_time = None

        # 开启流式输出，精准测算延迟
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.1,
            stream=True 
        )

        print("🤖 模型回答: ", end="", flush=True)
        token_count = 0
        
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                if first_token_time is None:
                    first_token_time = time.time() # 记录首字到达时间
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                token_count += 1
                
        end_time = time.time()
        print("\n")

        # 计算系统指标
        ttft = (first_token_time - start_time) * 1000 if first_token_time else 0
        total_time = end_time - start_time
        tps = token_count / total_time if total_time > 0 else 0

        print("-" * 40)
        print("📊 性能指标 (Baseline):")
        print(f"⏱️  首字延迟 (TTFT): {ttft:.2f} ms")
        print(f"⏱️  总生成耗时:    {total_time:.2f} s")
        print(f"🚀  生成吞吐量:    {tps:.2f} tokens/sec")
        print("-" * 40)
        print("✅ 测试成功！大模型接口工作正常。")

    except Exception as e:
        print(f"\n❌ 连接失败。请检查大模型服务是否已启动，或者端口是否正确。详细报错：\n{e}")

if __name__ == "__main__":
    test_local_llm()