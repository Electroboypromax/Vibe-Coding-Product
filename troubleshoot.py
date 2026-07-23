import os
import sys
import subprocess
import socket
import requests


def test_port_listening(port=8000):
    """
    测试端口是否正在监听
    """
    print("=" * 60)
    print("排查1: 端口监听状态")
    print("=" * 60)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("localhost", port))
        sock.close()

        if result == 0:
            print(f"✓ 端口 {port} 正在监听")
            return True
        else:
            print(f"✗ 端口 {port} 未在监听")
            print("  可能原因:")
            print("  - start.py 服务未启动")
            print("  - 服务启动失败")
            print("  - 端口被其他程序占用")
            return False

    except Exception as e:
        print(f"✗ 端口检测失败: {e}")
        return False


def test_service_response(port=8000):
    """
    测试服务是否能正常响应
    """
    print()
    print("=" * 60)
    print("排查2: 服务响应测试")
    print("=" * 60)

    endpoints = [
        f"http://localhost:{port}/",
        f"http://localhost:{port}/health",
        f"http://localhost:{port}/v1/models",
    ]

    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, timeout=5)
            print(f"✓ {endpoint}")
            print(f"   状态码: {response.status_code}")
            if response.status_code == 200:
                print(f"   响应: {response.text[:100]}...")
        except requests.ConnectionError:
            print(f"✗ {endpoint} - 连接失败")
        except requests.Timeout:
            print(f"✗ {endpoint} - 请求超时")
        except Exception as e:
            print(f"✗ {endpoint} - {e}")


def test_cors_headers(port=8000):
    """
    测试CORS头是否正确配置
    """
    print()
    print("=" * 60)
    print("排查3: CORS配置测试")
    print("=" * 60)

    try:
        endpoint = f"http://localhost:{port}/"
        response = requests.options(endpoint)

        cors_headers = [
            "Access-Control-Allow-Origin",
            "Access-Control-Allow-Methods",
            "Access-Control-Allow-Headers",
        ]

        print(f"OPTIONS请求状态码: {response.status_code}")

        for header in cors_headers:
            value = response.headers.get(header)
            if value:
                print(f"✓ {header}: {value}")
            else:
                print(f"✗ {header}: 未设置")

        if response.status_code == 200:
            print("✓ CORS配置正常")
            return True
        else:
            print("✗ CORS配置异常")
            return False

    except Exception as e:
        print(f"✗ CORS测试失败: {e}")
        return False


def test_chat_completion(port=8000):
    """
    测试聊天补全接口
    """
    print()
    print("=" * 60)
    print("排查4: 聊天补全接口测试")
    print("=" * 60)

    try:
        endpoint = f"http://localhost:{port}/v1/chat/completions"
        payload = {
            "model": "kimi-k2.6",
            "messages": [{"role": "user", "content": "Hello"}]
        }

        print("  注意: 首次请求会触发Kimi适配器初始化，可能需要30-60秒")
        print("  请确保Edge浏览器窗口已打开并完成Kimi页面加载")
        print()

        response = requests.post(endpoint, json=payload, timeout=120)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            print("✓ 接口响应正常")
            data = response.json()
            print(f"  响应ID: {data.get('id')}")
            print(f"  模型: {data.get('model')}")
            if data.get("choices"):
                content = data["choices"][0]["message"]["content"]
                print(f"  回复内容: {content[:100]}...")
            return True
        elif response.status_code == 503:
            print("✗ 服务初始化失败")
            print(f"  错误信息: {response.text}")
            return False
        else:
            print(f"✗ 接口返回错误: {response.text}")
            return False

    except requests.ConnectionError:
        print("✗ 无法连接到服务")
        return False
    except requests.Timeout:
        print("✗ 请求超时")
        print("  可能原因:")
        print("  - Kimi页面加载时间过长")
        print("  - 网络连接问题")
        print("  - 需要手动在Edge浏览器中完成登录")
        return False
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False


def test_simplified_api(port=8000):
    """
    测试简化API接口
    """
    print()
    print("=" * 60)
    print("排查5: 简化API接口测试")
    print("=" * 60)

    try:
        endpoint = f"http://localhost:{port}/api/chat/send"
        payload = {"message": "Hello"}

        response = requests.post(endpoint, json=payload, timeout=10)
        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            print("✓ 简化接口响应正常")
            data = response.json()
            print(f"  代码: {data.get('code')}")
            if data.get("response"):
                print(f"  响应: {data['response'][:100]}...")
            return True
        else:
            print(f"✗ 接口返回错误: {response.text}")
            return False

    except requests.ConnectionError:
        print("✗ 无法连接到服务")
        return False
    except requests.Timeout:
        print("✗ 请求超时")
        return False
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False


def check_firewall_rules():
    """
    检查防火墙规则
    """
    print()
    print("=" * 60)
    print("排查6: 防火墙规则检查")
    print("=" * 60)

    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in"],
            capture_output=True,
            text=True,
            timeout=30
        )

        lines = result.stdout.split("\n")
        found_8000 = False

        for line in lines:
            if "8000" in line:
                found_8000 = True
                print(f"✓ 找到端口8000相关规则: {line.strip()}")

        if not found_8000:
            print("✗ 未找到端口8000的防火墙规则")
            print("  建议添加入站规则允许端口8000")

    except subprocess.TimeoutExpired:
        print("✗ 防火墙检查超时")
    except Exception as e:
        print(f"✗ 防火墙检查失败: {e}")


def show_chatbox_config_guide(port=8000):
    """
    显示Chatbox配置指南
    """
    print()
    print("=" * 60)
    print("Chatbox配置指南")
    print("=" * 60)
    print()
    print("在Chatbox应用中配置自定义API:")
    print()
    print("配置项:")
    print(f"  API Base URL: http://localhost:{port}/v1")
    print(f"  API Key: 任意字符串（当前版本未启用认证）")
    print(f"  Model ID: kimi-k2.6")
    print()
    print("完整API端点:")
    print(f"  聊天补全: http://localhost:{port}/v1/chat/completions")
    print(f"  流式聊天: http://localhost:{port}/v1/chat/completions/stream")
    print(f"  模型列表: http://localhost:{port}/v1/models")
    print()
    print("简化接口（可选）:")
    print(f"  POST http://localhost:{port}/api/chat/send")
    print(f"  请求体: {{\"message\": \"你的问题\"}}")


def main():
    """
    运行所有故障排查测试
    """
    print("=" * 60)
    print("AI Service Adapter - 故障排查工具")
    print("=" * 60)
    print()

    port = 8000
    results = []

    results.append(test_port_listening(port))
    test_service_response(port)
    results.append(test_cors_headers(port))
    results.append(test_chat_completion(port))
    results.append(test_simplified_api(port))
    check_firewall_rules()
    show_chatbox_config_guide(port)

    print()
    print("=" * 60)
    print("排查结果汇总")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    print(f"通过: {passed}/{total}")
    print()

    if passed == total:
        print("✓ 所有测试通过，服务配置正常")
        print("  请检查Chatbox应用的配置是否正确")
    else:
        print("✗ 部分测试未通过，请按照以下建议排查:")
        print()
        print("常见问题及解决方案:")
        print()
        print("1. 服务未启动:")
        print("   运行命令: python start.py")
        print("   等待浏览器窗口出现并完成初始化")
        print()
        print("2. CORS问题:")
        print("   已在代码中添加CORS配置，重启服务即可生效")
        print()
        print("3. 防火墙问题:")
        print("   运行命令: netsh advfirewall firewall add rule name=\"AI Adapter\" dir=in action=allow protocol=TCP localport=8000")
        print()
        print("4. Chatbox配置错误:")
        print("   API Base URL必须以/v1结尾: http://localhost:8000/v1")
        print()
        print("5. 网络问题:")
        print("   尝试使用127.0.0.1代替localhost")
        print("   确保没有使用代理软件")


if __name__ == "__main__":
    main()