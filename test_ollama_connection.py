"""
Ollama连接测试脚本

用于测试Ollama服务是否正常工作
"""

import requests
import json
from urllib.parse import urlparse


def test_ollama_connection(base_url: str = "http://127.0.0.1:11434"):
    """
    测试Ollama连接

    Args:
        base_url: Ollama基础URL
    """
    print("\n" + "=" * 80)
    print("Ollama连接测试")
    print("=" * 80)
    print(f"Ollama服务地址: {base_url}\n")

    # 测试1: 检查服务是否运行
    print("【测试1】检查Ollama服务状态")
    print("-" * 80)
    try:
        response = requests.get(f"{base_url}/", timeout=10)
        print(f"✓ Ollama服务正在运行 (状态码: {response.status_code})")
    except requests.exceptions.ConnectionError:
        print(f"✗ 连接被拒绝，Ollama服务可能未启动")
        print(f"💡 解决方案:")
        print(f"   1. 检查Ollama是否已安装: ollama --version")
        print(f"   2. 启动Ollama服务: ollama serve")
        print(f"   3. 确认Ollama监听地址是否正确")
        return False
    except requests.exceptions.Timeout:
        print(f"✗ 连接超时")
        print(f"💡 解决方案:")
        print(f"   1. 检查网络连接")
        print(f"   2. 尝试使用本地服务 (http://127.0.0.1:11434)")
        return False
    except Exception as e:
        print(f"✗ 未知错误: {type(e).__name__}: {str(e)}")
        return False

    # 测试2: 获取可用模型列表
    print("\n【测试2】获取可用模型列表")
    print("-" * 80)
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=10)
        if response.status_code == 200:
            data = response.json()
            models = data.get('models', [])
            if models:
                print(f"✓ 发现 {len(models)} 个可用模型:")
                for i, model in enumerate(models, 1):
                    model_name = model.get('name', 'unknown')
                    model_size = model.get('size', 0) / (1024**3)  # 转换为GB
                    print(f"   {i}. {model_name} ({model_size:.2f} GB)")
            else:
                print(f"⚠ 未发现可用模型")
                print(f"💡 解决方案:")
                print(f"   1. 拉取模型: ollama pull gemma3:27b")
                print(f"   2. 或其他模型: ollama pull llama2")
        else:
            print(f"✗ 获取模型列表失败 (状态码: {response.status_code})")
            return False
    except Exception as e:
        print(f"✗ 获取模型列表失败: {type(e).__name__}: {str(e)}")
        return False

    # 测试3: 测试模型推理
    print("\n【测试3】测试模型推理")
    print("-" * 80)
    if models:
        # 使用第一个可用模型进行测试
        test_model = models[0]['name']
        print(f"使用模型: {test_model}")

        try:
            payload = {
                "model": test_model,
                "prompt": "你好，请回复'测试成功'。",
                "stream": False
            }
            response = requests.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                reply = result.get('response', '').strip()
                print(f"✓ 模型推理成功!")
                print(f"响应内容: {reply}")
                print(f"推理耗时: {result.get('total_duration', 0) / 1e9:.2f} 秒")
                print(f"评估耗时: {result.get('eval_duration', 0) / 1e9:.2f} 秒")
            else:
                print(f"✗ 模型推理失败 (状态码: {response.status_code})")
                print(f"响应内容: {response.text[:200]}")
                return False
        except requests.exceptions.Timeout:
            print(f"✗ 模型推理超时")
            print(f"💡 可能原因:")
            print(f"   1. 模型太大，首次加载需要较长时间")
            print(f"   2. 服务器性能不足")
            print(f"   3. 尝试使用较小的模型")
            return False
        except Exception as e:
            print(f"✗ 模型推理失败: {type(e).__name__}: {str(e)}")
            return False
    else:
        print(f"⚠ 跳过推理测试（无可用模型）")

    # 测试4: 测试OpenAI兼容API
    print("\n【测试4】测试OpenAI兼容API")
    print("-" * 80)
    if models:
        test_model = models[0]['name']
        print(f"使用模型: {test_model}")

        try:
            import openai
            client = openai.OpenAI(
                base_url=f"{base_url}/v1",
                api_key="ollama"  # Ollama不需要真实API密钥
            )

            response = client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "你好，请回复'API测试成功'。"}],
                max_tokens=50,
                stream=False
            )

            if response.choices and len(response.choices) > 0:
                reply = response.choices[0].message.content
                print(f"✓ OpenAI兼容API测试成功!")
                print(f"响应内容: {reply}")
            else:
                print(f"✗ OpenAI兼容API测试失败: 响应格式错误")
                return False
        except Exception as e:
            print(f"✗ OpenAI兼容API测试失败: {type(e).__name__}: {str(e)}")
            return False
    else:
        print(f"⚠ 跳过API测试（无可用模型）")

    print("\n" + "=" * 80)
    print("✓ 所有测试通过!")
    print("=" * 80)
    return True


if __name__ == "__main__":
    import sys

    # 默认测试地址
    test_url = "http://127.0.0.1:11434"

    # 如果提供了命令行参数，使用提供的地址
    if len(sys.argv) > 1:
        test_url = sys.argv[1]

    # 执行测试
    success = test_ollama_connection(test_url)

    # 返回退出码
    sys.exit(0 if success else 1)

