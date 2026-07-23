import logging
import time
import asyncio
import os
import uuid
from typing import Optional, AsyncGenerator
from dotenv import load_dotenv
from .base_adapter import BaseAIAdapter
from .types import AdapterConfig, ChatMessage
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
    ElementHandle,
)

load_dotenv()

logger = logging.getLogger(__name__)


class ChatGPTAdapter(BaseAIAdapter):
    """
    ChatGPT Web AI服务适配器

    实现对ChatGPT网页的自动化操作，包括：
    - 浏览器初始化与生命周期管理（带反检测配置）
    - Cloudflare绕过处理
    - 登录状态检测机制（支持手动登录）
    - 输入框定位与消息发送
    - 回复状态判断与内容提取（支持Markdown格式）
    - 错误处理与重试机制

    用户需要在浏览器窗口中手动完成登录操作，系统会检测登录状态并阻止未登录用户发送消息。
    """

    CHATGPT_URL = "https://chatgpt.com"
    MODEL_NAME = "GPT-4o"

    SELECTORS = {
        "input_box": "textarea[placeholder*='Message'], textarea[placeholder*='消息'], textarea[placeholder*='输入'], [role='textbox'], div[contenteditable='true'], .ProseMirror",
        "send_button": "button[aria-label*='Send'], button[aria-label*='发送'], button[data-testid*='send'], button:has(svg), [class*='send']",
        "chat_message": "[data-message-id], [class*='message'], [data-testid*='message'], article",
        "assistant_message": "[data-author-role='assistant'], [class*='assistant'], [data-author='assistant'], .gpt-message",
        "message_content": ".markdown, [class*='markdown'], [class*='content'], .prose, [class*='message-content'], [data-testid*='text'], [class*='whitespace-pre-wrap']",
        "loading_indicator": ".loading, [class*='loading'], [aria-busy='true'], [class*='spin'], [class*='typing'], [data-testid*='typing'], [class*='thinking']",
        "stop_button": "button[aria-label*='Stop'], button:has-text('Stop'), button:has-text('停止'), [class*='stop']",
        "login_button": "button:has-text('Log in'), button:has-text('登录'), [href*='/auth/login']",
        "email_input": "input[type='email'], input[placeholder*='email'], input[placeholder*='邮箱'], input[name='email'], input[name='username']",
        "phone_input": "input[type='tel'], input[placeholder*='phone'], input[placeholder*='phone number'], input[name='phoneNumber'], input[placeholder*='手机号']",
        "password_input": "input[type='password'], input[placeholder*='password'], input[placeholder*='密码']",
        "continue_button": "button:has-text('Continue'), button:has-text('继续'), button[type='submit']",
        "next_button": "button:has-text('Next'), button:has-text('下一步')",
    }

    def __init__(self, config: AdapterConfig = None):
        if config is None:
            config = AdapterConfig(headless=False)
        
        import os
        project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        profile_dir = os.path.join(project_dir, "edge_profile_chatgpt")
        os.makedirs(profile_dir, exist_ok=True)
        self.profile_dir = profile_dir
        self.cdp_url = "http://localhost:9222"
        
        super().__init__(config)
        self.last_message_count = 0
        self.last_reply_text = ""
        self.stable_count = 0
        self._conversation_summary = ""
        self.max_wait_time = config.timeout
        self.is_logged_in = False

    async def initialize(self) -> None:
        """
        初始化ChatGPT服务适配器

        仅启动playwright，浏览器连接延迟到首次发送消息时进行。
        用户需要先手动启动Edge浏览器并完成Cloudflare验证和登录。
        """
        logger.info("Initializing ChatGPT adapter...")

        try:
            self.playwright = await async_playwright().start()
            logger.info("Playwright started successfully")
            logger.info("")
            logger.info("=" * 70)
            logger.info("CHATGPT ADAPTER READY")
            logger.info("=" * 70)
            logger.info("Before using ChatGPT, please:")
            logger.info("")
            logger.info("1. Start Edge browser with this command:")
            logger.info(f"   & 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' --remote-debugging-port=9222 --user-data-dir='{self.profile_dir}'")
            logger.info("")
            logger.info("2. In the browser:")
            logger.info("   - Navigate to https://chatgpt.com")
            logger.info("   - Complete Cloudflare security verification")
            logger.info("   - Log in with your account")
            logger.info("")
            logger.info("3. Once logged in, you can send messages via the API")
            logger.info("=" * 70)

        except Exception as e:
            logger.error(f"Failed to initialize ChatGPT adapter: {e}")
            raise

    async def _connect_to_browser(self) -> bool:
        """
        连接到用户手动启动的Edge浏览器实例

        Returns:
            bool: True表示连接成功，False表示连接失败
        """
        if self.page:
            return True

        import socket
        def is_port_available(port):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            return result == 0

        if not is_port_available(9222):
            logger.error("Edge browser not detected on port 9222")
            logger.error("Please start Edge browser with remote debugging enabled")
            return False

        try:
            logger.info(f"Connecting to Edge via CDP: {self.cdp_url}")
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)

            if self.browser.contexts:
                self.context = self.browser.contexts[0]
                logger.info("Using existing browser context")
            else:
                self.context = await self.browser.new_context()
                logger.info("Created new browser context")

            if self.context.pages:
                self.page = self.context.pages[0]
                logger.info(f"Using existing page: {self.page.url}")
            else:
                self.page = await self.context.new_page()
                logger.info("Created new page")

            current_url = self.page.url
            logger.info(f"Current page URL: {current_url}")

            if "chatgpt.com" not in current_url:
                logger.info(f"Warning: Current page is not ChatGPT.")
                logger.info(f"Navigating to https://chatgpt.com...")
                await self.page.goto("https://chatgpt.com", wait_until="networkidle", timeout=60000)
                logger.info("Navigated to ChatGPT successfully")

            logger.info("Browser connection successful")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to browser: {e}")
            return False

    async def _handle_cloudflare(self) -> None:
        """
        处理Cloudflare反爬虫验证

        实现多种策略绕过Cloudflare验证：
        1. 等待挑战前置页完成渲染（阶段A）
        2. 处理Turnstile验证（阶段B）
        3. 处理hCaptcha/reCAPTCHA挑战
        4. 处理人机验证checkbox
        5. 如果验证失败，等待用户手动完成验证
        """
        try:
            logger.debug("Handling Cloudflare verification...")

            for attempt in range(5):
                await asyncio.sleep(3)

                cloudflare_detected = await self._detect_cloudflare()
                if not cloudflare_detected:
                    logger.info("Cloudflare verification not detected or already passed")
                    break

                logger.info(f"Cloudflare verification detected (attempt {attempt + 1}/5), attempting to bypass...")

                await self._wait_for_challenge_page()

                if await self._solve_turnstile():
                    logger.info("Turnstile verification solved")
                    await asyncio.sleep(5)
                    continue

                if await self._solve_hcaptcha():
                    logger.info("hCaptcha verification solved")
                    await asyncio.sleep(5)
                    continue

                if await self._solve_recaptcha():
                    logger.info("reCAPTCHA verification solved")
                    await asyncio.sleep(5)
                    continue

                if await self._click_challenge_checkbox():
                    logger.info("Challenge checkbox clicked")
                    await asyncio.sleep(8)
                    continue

                logger.info("Waiting for Cloudflare automatic verification...")
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=30000)
                except PlaywrightTimeoutError:
                    logger.info("Cloudflare verification timeout, continuing...")

                await asyncio.sleep(5)

                cloudflare_detected = await self._detect_cloudflare()
                if not cloudflare_detected:
                    logger.info("Cloudflare verification completed")
                    break

            cloudflare_detected = await self._detect_cloudflare()
            if cloudflare_detected:
                logger.info("=" * 70)
                logger.info("CLOUDFLARE VERIFICATION REQUIRED")
                logger.info("=" * 70)
                logger.info("Please complete the Cloudflare verification in the browser window.")
                logger.info("This may include:")
                logger.info("  - Clicking a checkbox to prove you are human")
                logger.info("  - Completing a CAPTCHA challenge")
                logger.info("  - Solving image verification")
                logger.info("=" * 70)
                logger.info("Waiting for Cloudflare verification...")
                logger.info("=" * 70)

                for i in range(300):
                    await asyncio.sleep(2)
                    cloudflare_detected = await self._detect_cloudflare()
                    if not cloudflare_detected:
                        logger.info("=" * 70)
                        logger.info("CLOUDFLARE VERIFICATION COMPLETED!")
                        logger.info("=" * 70)
                        break

                    if (i + 1) % 30 == 0:
                        logger.info(f"Waiting for Cloudflare verification... ({(i + 1) * 2} seconds elapsed)")

            await asyncio.sleep(3)
            logger.debug("Cloudflare handling done")

        except Exception as e:
            logger.warning(f"Error handling Cloudflare: {e}")

    async def _wait_for_challenge_page(self) -> None:
        """
        等待挑战前置页完成渲染（阶段A）

        Cloudflare通常会先显示"请稍候..."页面，然后才显示真正的验证组件。
        这个方法等待前置页完成或验证组件出现。
        """
        try:
            logger.debug("Waiting for challenge page to render...")

            for i in range(30):
                page_title = await self.page.title()
                page_text = await self.page.evaluate("() => document.body.innerText")

                if "请稍候" in page_text or "just a moment" in page_text.lower():
                    logger.debug("Waiting for challenge page... (stage A)")
                    await asyncio.sleep(1)
                    continue

                turnstile_elements = await self.page.query_selector_all(".cf-turnstile, [data-sitekey], iframe[src*='turnstile']")
                if turnstile_elements:
                    logger.debug("Turnstile elements found, challenge page ready")
                    break

                checkbox_elements = await self.page.query_selector_all("input[type='checkbox'], button[id*='challenge']")
                if checkbox_elements:
                    logger.debug("Challenge checkbox found, challenge page ready")
                    break

                await asyncio.sleep(1)

            logger.debug("Challenge page wait completed")
        except Exception as e:
            logger.debug(f"Error waiting for challenge page: {e}")

    async def _detect_cloudflare(self) -> bool:
        """
        检测是否存在Cloudflare验证页面
        """
        try:
            cloudflare_selectors = [
                ".cf-browser-verification",
                "[id='challenge-stage']",
                "[class*='cloudflare']",
                "[id*='cf-']",
                "[name='cf_chl_jschl']",
                ".cf-turnstile",
                "[data-sitekey]",
                ".h-captcha",
                ".g-recaptcha",
            ]

            for selector in cloudflare_selectors:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    logger.debug(f"Cloudflare detected via selector: {selector}")
                    return True

            page_title = await self.page.title()
            if "cloudflare" in page_title.lower():
                logger.debug(f"Cloudflare detected via page title: {page_title}")
                return True

            current_url = self.page.url
            if "cf-" in current_url.lower() or "cloudflare" in current_url.lower():
                logger.debug(f"Cloudflare detected via URL: {current_url}")
                return True

            return False
        except Exception as e:
            logger.debug(f"Error detecting Cloudflare: {e}")
            return False

    async def _solve_turnstile(self) -> bool:
        """
        尝试解决Cloudflare Turnstile验证
        """
        try:
            turnstile_selectors = [
                ".cf-turnstile",
                "[data-sitekey]",
                "iframe[src*='turnstile']",
            ]

            for selector in turnstile_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    logger.info(f"Found Turnstile element using selector: {selector}")
                    
                    checkbox = await self.page.query_selector("input[type='checkbox'][id*='challenge']")
                    if checkbox:
                        try:
                            await checkbox.click()
                            logger.info("Clicked Turnstile checkbox")
                            return True
                        except Exception:
                            logger.debug("Turnstile checkbox not clickable")

                    await asyncio.sleep(5)
                    return True

            return False
        except Exception as e:
            logger.debug(f"Error solving Turnstile: {e}")
            return False

    async def _solve_hcaptcha(self) -> bool:
        """
        尝试解决hCaptcha验证
        """
        try:
            hcaptcha_selectors = [
                ".h-captcha",
                "iframe[src*='hcaptcha']",
                "[data-hcaptcha-sitekey]",
            ]

            for selector in hcaptcha_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    logger.info(f"Found hCaptcha element using selector: {selector}")
                    
                    checkbox = await self.page.query_selector("input[type='checkbox']")
                    if checkbox:
                        try:
                            await checkbox.click()
                            logger.info("Clicked hCaptcha checkbox")
                            await asyncio.sleep(5)
                            return True
                        except Exception:
                            logger.debug("hCaptcha checkbox not clickable")

            return False
        except Exception as e:
            logger.debug(f"Error solving hCaptcha: {e}")
            return False

    async def _solve_recaptcha(self) -> bool:
        """
        尝试解决reCAPTCHA验证
        """
        try:
            recaptcha_selectors = [
                ".g-recaptcha",
                "iframe[src*='recaptcha']",
                "[data-recaptcha-sitekey]",
            ]

            for selector in recaptcha_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    logger.info(f"Found reCAPTCHA element using selector: {selector}")
                    
                    checkbox = await self.page.query_selector("input[type='checkbox']")
                    if checkbox:
                        try:
                            await checkbox.click()
                            logger.info("Clicked reCAPTCHA checkbox")
                            await asyncio.sleep(5)
                            return True
                        except Exception:
                            logger.debug("reCAPTCHA checkbox not clickable")

            return False
        except Exception as e:
            logger.debug(f"Error solving reCAPTCHA: {e}")
            return False

    async def _click_challenge_checkbox(self) -> bool:
        """
        尝试点击Cloudflare挑战checkbox
        """
        try:
            challenge_selectors = [
                "input[type='checkbox'][id*='challenge']",
                "button[id*='challenge']",
                ".challenge-checkbox",
                "[class*='captcha-checkbox']",
                "input[type='checkbox']",
            ]

            for selector in challenge_selectors:
                element = await self.page.query_selector(selector)
                if element:
                    try:
                        await element.click()
                        logger.info(f"Clicked challenge checkbox using selector: {selector}")
                        return True
                    except Exception:
                        logger.debug(f"Challenge checkbox not clickable using selector: {selector}")

            return False
        except Exception as e:
            logger.debug(f"Error clicking challenge checkbox: {e}")
            return False

    async def _handle_login(self) -> None:
        """
        处理登录状态

        检测当前页面是否已登录，如果未登录则等待用户手动完成登录。
        登录状态通过以下方式检测：
        1. 是否存在可输入的聊天输入框
        2. 是否存在消息历史区域
        3. 是否存在侧边栏/对话列表
        4. 是否不存在登录按钮或登录表单

        如果检测到未登录状态，程序会等待用户手动登录，直到检测到登录成功。
        """
        try:
            current_url = self.page.url
            logger.info(f"Current URL: {current_url}")

            await asyncio.sleep(3)

            await self._print_page_state()

            login_status = await self._check_login_status()
            logger.info(f"Initial login status: {'LOGGED IN' if login_status else 'NOT LOGGED IN'}")

            if login_status:
                logger.info("Already logged in - chat page with input box found")
                self.is_logged_in = True
                return

            logger.info("=" * 70)
            logger.info("LOGIN REQUIRED - PLEASE MANUALLY LOG IN")
            logger.info("=" * 70)
            logger.info("Please complete the login in the browser window.")
            logger.info("Steps:")
            logger.info("  1. Click the '登录' or 'Log in' button")
            logger.info(f"  2. Enter your email: {os.environ.get('CHATGPT_EMAIL', '[your_email]')}")
            logger.info("  3. Enter your password")
            logger.info("  4. Complete any verification steps (if required)")
            logger.info("=" * 70)
            logger.info("Waiting for login...")
            logger.info("=" * 70)

            for i in range(600):
                await asyncio.sleep(2)

                login_status = await self._check_login_status()
                if login_status:
                    logger.info("=" * 70)
                    logger.info("LOGIN DETECTED!")
                    logger.info("=" * 70)
                    self.is_logged_in = True
                    return

                if (i + 1) % 30 == 0:
                    logger.info(f"Waiting for login... ({(i + 1) * 2} seconds elapsed)")

            logger.warning("=" * 70)
            logger.warning("LOGIN TIMEOUT")
            logger.warning("=" * 70)
            logger.warning("Login timed out after 20 minutes.")
            logger.warning("Please restart the application and try again.")
            logger.warning("=" * 70)

        except Exception as e:
            logger.error(f"Error handling login: {e}")

    async def _check_login_status(self) -> bool:
        """
        检测当前登录状态

        通过多种指标综合判断用户是否已成功登录：
        1. 页面URL是否为聊天页面（不含login/auth）
        2. 是否存在可见且可交互的聊天输入框
        3. 是否存在消息历史或对话列表
        4. 是否不存在登录按钮或登录表单

        Returns:
            bool: True表示已登录，False表示未登录
        """
        try:
            await asyncio.sleep(2)

            current_url = self.page.url
            logger.info(f"Current URL for login check: {current_url}")
            
            is_login_url = 'login' in current_url.lower() or 'sign_in' in current_url.lower() or 'auth' in current_url.lower()

            if is_login_url:
                logger.debug("Login page detected via URL")
                return False

            input_box = await self.page.query_selector(self.SELECTORS["input_box"])
            has_visible_input = False
            if input_box:
                is_input_visible = await self.page.evaluate("(el) => el.offsetParent !== null", input_box)
                is_input_enabled = await self.page.evaluate("(el) => !el.disabled", input_box)
                has_visible_input = is_input_visible and is_input_enabled
                logger.info(f"Input box found: visible={is_input_visible}, enabled={is_input_enabled}, has_visible_input={has_visible_input}")
            else:
                logger.info("No input box found with main selector, searching for textarea...")
                textareas = await self.page.query_selector_all("textarea")
                logger.info(f"Found {len(textareas)} textarea elements")
                for i, ta in enumerate(textareas):
                    placeholder = await self.page.evaluate("(el) => el.placeholder || ''", ta)
                    visible = await self.page.evaluate("(el) => el.offsetParent !== null", ta)
                    logger.info(f"  Textarea {i}: placeholder='{placeholder}', visible={visible}")

            textarea = await self.page.query_selector("textarea")
            has_textarea = False
            if textarea:
                textarea_visible = await self.page.evaluate("(el) => el.offsetParent !== null", textarea)
                textarea_enabled = await self.page.evaluate("(el) => !el.disabled", textarea)
                has_textarea = textarea_visible and textarea_enabled
                logger.info(f"Textarea found: visible={textarea_visible}, enabled={textarea_enabled}")

            chat_indicators = [
                "[data-message-id]",
                "[class*='conversation']",
                "[class*='chat-history']",
                "[class*='message-history']",
                "[class*='sidebar']",
                "[class*='chat-list']",
                ".prose",
                "[data-author-role]",
                ".markdown",
                "[class*='message-content']",
                "[class*='response-message']",
            ]

            chat_indicator_count = 0
            for selector in chat_indicators:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    chat_indicator_count += 1

            logger.info(f"Chat indicators found: {chat_indicator_count}")

            login_indicators = [
                "button:has-text('Log in')",
                "button:has-text('登录')",
                "button:has-text('Sign in')",
                "button:has-text('免费注册')",
                "input[type='email']",
                "input[type='password']",
                "[href*='/auth/login']",
            ]

            login_indicator_count = 0
            for selector in login_indicators:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    login_indicator_count += 1

            logger.info(f"Login indicators found: {login_indicator_count}")

            has_valid_input = has_visible_input or has_textarea

            is_logged_in = False

            if has_valid_input:
                if login_indicator_count == 0:
                    is_logged_in = True
                elif login_indicator_count <= 1:
                    logger.info("Found some login indicators but input is valid, checking if they're hidden...")
                    is_logged_in = True
            elif chat_indicator_count >= 2:
                is_logged_in = True
                logger.info("No valid input found but has chat indicators, assuming logged in")

            logger.info(f"Login status check result: has_valid_input={has_valid_input}, login_indicators={login_indicator_count}, chat_indicators={chat_indicator_count}, is_logged_in={is_logged_in}")

            return is_logged_in

        except Exception as e:
            logger.warning(f"Error checking login status: {e}")
            return False

    async def _is_chat_page(self) -> bool:
        """
        判断当前页面是否为聊天页面

        聊天页面特征：
        - 有消息历史区域
        - 有聊天列表/侧边栏
        - 输入框有特定特征
        - 没有登录按钮或登录相关元素
        """
        try:
            chat_page_indicators = [
                "[data-message-id]",
                "[class*='conversation']",
                "[class*='chat-history']",
                "[class*='message-history']",
                "[class*='sidebar']",
                "[class*='chat-list']",
                ".prose",
                "[data-author-role]",
                ".markdown",
            ]

            chat_indicator_count = 0
            for selector in chat_page_indicators:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    chat_indicator_count += 1

            login_page_indicators = [
                "button:has-text('Log in')",
                "button:has-text('登录')",
                "button:has-text('Sign in')",
                "button:has-text('Sign In')",
                "button:has-text('免费注册')",
                "input[type='email']",
                "input[type='password']",
                "[href*='/auth/login']",
            ]

            login_indicator_count = 0
            for selector in login_page_indicators:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    login_indicator_count += 1

            logger.debug(f"Chat indicators: {chat_indicator_count}, Login indicators: {login_indicator_count}")

            is_chat = chat_indicator_count >= 3 and login_indicator_count < 2
            return is_chat

        except Exception as e:
            logger.warning(f"Error checking if chat page: {e}")
            return False

    async def _print_page_state(self) -> None:
        """
        打印页面状态信息，用于调试
        """
        try:
            page_info = await self.page.evaluate("""
                () => {
                    const buttons = [];
                    document.querySelectorAll('button').forEach((btn, i) => {
                        if (btn.textContent && btn.textContent.trim()) {
                            buttons.push({
                                index: i,
                                text: btn.textContent.trim().substring(0, 50),
                                className: btn.className.substring(0, 100),
                                type: btn.type,
                                disabled: btn.disabled,
                                visible: btn.offsetParent !== null,
                            });
                        }
                    });

                    const inputs = [];
                    document.querySelectorAll('input').forEach((inp, i) => {
                        inputs.push({
                            index: i,
                            type: inp.type,
                            name: inp.name,
                            placeholder: inp.placeholder,
                            className: inp.className.substring(0, 100),
                            value: inp.value ? '***' : '',
                            visible: inp.offsetParent !== null,
                        });
                    });

                    const textareas = [];
                    document.querySelectorAll('textarea').forEach((ta, i) => {
                        textareas.push({
                            index: i,
                            placeholder: ta.placeholder,
                            className: ta.className.substring(0, 100),
                            value: ta.value ? '***' : '',
                            visible: ta.offsetParent !== null,
                        });
                    });

                    const links = [];
                    document.querySelectorAll('a').forEach((link, i) => {
                        if (link.textContent && link.textContent.trim()) {
                            links.push({
                                index: i,
                                text: link.textContent.trim().substring(0, 50),
                                href: link.href.substring(0, 100),
                            });
                        }
                    });

                    return {
                        title: document.title,
                        url: window.location.href,
                        buttons: buttons.slice(0, 20),
                        inputs: inputs.slice(0, 10),
                        textareas: textareas.slice(0, 5),
                        links: links.slice(0, 10),
                    };
                }
            """)

            logger.info(f"=== Page State ===")
            logger.info(f"Title: {page_info.get('title', '')}")
            logger.info(f"URL: {page_info.get('url', '')}")
            logger.info(f"\nButtons ({len(page_info.get('buttons', []))}):")
            for btn in page_info.get('buttons', []):
                logger.info(f"  [{btn['index']}] '{btn['text']}' type={btn['type']} disabled={btn['disabled']} visible={btn['visible']}")
            logger.info(f"\nInputs ({len(page_info.get('inputs', []))}):")
            for inp in page_info.get('inputs', []):
                logger.info(f"  [{inp['index']}] type={inp['type']} name={inp['name']} placeholder='{inp['placeholder']}'")
            logger.info(f"\nTextareas ({len(page_info.get('textareas', []))}):")
            for ta in page_info.get('textareas', []):
                logger.info(f"  [{ta['index']}] placeholder='{ta['placeholder']}'")
            logger.info(f"=== End Page State ===")

        except Exception as e:
            logger.warning(f"Error printing page state: {e}")

    async def _ensure_input_ready(self) -> None:
        """
        确保输入框可用

        等待输入框出现并确保可以输入。
        """
        logger.debug("Ensuring input is ready...")

        try:
            await self.page.wait_for_selector(
                self.SELECTORS["input_box"],
                timeout=self.config.page_load_timeout * 1000,
            )
            logger.debug("Input box found")
        except PlaywrightTimeoutError:
            logger.warning("Input box not found immediately, searching...")
            await asyncio.sleep(3)
            
            all_inputs = await self.page.query_selector_all("textarea, [role='textbox'], div[contenteditable='true']")
            logger.info(f"Found {len(all_inputs)} potential input elements after wait")
            for i, el in enumerate(all_inputs[:5]):
                tag_name = await self.page.evaluate("(el) => el.tagName", el)
                placeholder = await self.page.evaluate("(el) => el.placeholder || ''", el)
                role = await self.page.evaluate("(el) => el.getAttribute('role') || ''", el)
                logger.info(f"  Input {i}: tag={tag_name}, placeholder='{placeholder}', role='{role}'")

    async def send_message(self, messages: list[ChatMessage]) -> str:
        """
        发送消息并获取AI回复

        Args:
            messages: 消息列表，包含对话历史

        Returns:
            AI回复的文本内容（Markdown格式）

        Raises:
            RuntimeError: 如果用户未登录或登录状态失效，或浏览器未连接
        """
        await self.acquire_lock()
        try:
            # 先提取用户消息，用于命名请求拦截
            user_message = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    user_message = msg.get_text_content()
                    break

            if not user_message:
                logger.warning("No user message found, using last message")
                user_message = messages[-1].get_text_content() if messages else ""

            logger.debug(f"Selected user message to send: '{user_message[:50]}'...")

            # 拦截Chatbox的对话命名请求，避免发送到ChatGPT网页
            if self._is_naming_request(user_message):
                name = self._generate_conversation_name(messages)
                logger.info(f"Intercepted naming request, returning: '{name}'")
                return name

            connected = await self._connect_to_browser()
            if not connected:
                logger.error("Browser not connected. Cannot send message.")
                raise RuntimeError("Browser not connected. Please start Edge browser with remote debugging enabled first.")

            await self._ensure_input_ready()

            user_msg_count = sum(1 for msg in messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
            if user_msg_count == 1:
                await self._start_new_conversation()

            login_status = await self._check_login_status()
            if not login_status:
                logger.error("User is not logged in. Cannot send message.")
                raise RuntimeError("User is not logged in. Please complete login in the browser window first.")

            await self._type_message(user_message)

            await self._press_send()

            await self._wait_for_reply()

            await asyncio.sleep(2)
            await self._extract_conversation_summary()

            reply_text = await self._extract_reply_content()

            if reply_text:
                logger.debug(f"Received reply: {reply_text[:100]}...")
            else:
                logger.warning("Empty reply received")

            return reply_text

        finally:
            self.release_lock()

    async def send_message_stream(self, messages: list[ChatMessage]) -> AsyncGenerator[str, None]:
        """
        发送消息并以流式方式获取AI回复

        Args:
            messages: 消息列表，包含对话历史

        Yields:
            流式回复的文本片段

        Raises:
            RuntimeError: 如果用户未登录或登录状态失效，或浏览器未连接
        """
        await self.acquire_lock()
        try:
            # 先提取用户消息，用于命名请求拦截
            user_message = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    user_message = msg.get_text_content()
                    break

            if not user_message:
                user_message = messages[-1].get_text_content() if messages else ""

            logger.debug(f"Selected user message to send (stream): '{user_message[:50]}'...")

            # 拦截Chatbox的对话命名请求，避免发送到ChatGPT网页
            if self._is_naming_request(user_message):
                await self._wait_for_conversation_summary(timeout=15)
                name = self._generate_conversation_name(messages)
                logger.info(f"Intercepted naming request (stream), returning: '{name}'")
                yield name
                return

            connected = await self._connect_to_browser()
            if not connected:
                logger.error("Browser not connected. Cannot send message.")
                raise RuntimeError("Browser not connected. Please start Edge browser with remote debugging enabled first.")

            await self._ensure_input_ready()

            user_msg_count = sum(1 for msg in messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
            if user_msg_count == 1:
                await self._start_new_conversation()

            login_status = await self._check_login_status()
            if not login_status:
                logger.error("User is not logged in. Cannot send message.")
                raise RuntimeError("User is not logged in. Please complete login in the browser window first.")

            await self._type_message(user_message)

            await self._press_send()

            last_text = ""
            stable_count = 0

            for _ in range(int(self.max_wait_time)):
                current_text = await self._extract_reply_content(include_images=False)

                if current_text:
                    delta = current_text[len(last_text):]
                    if delta:
                        yield delta
                        stable_count = 0
                    else:
                        stable_count += 1

                    last_text = current_text

                    if stable_count > 10:
                        logger.debug("Reply stable for 10 checks, assuming complete")
                        break

                await asyncio.sleep(1)

            final_text = await self._extract_reply_content(include_images=True)
            if final_text and len(final_text) > len(last_text):
                yield final_text[len(last_text):]

            await asyncio.sleep(3)
            web_summary = await self._extract_web_summary()
            self._conversation_summary = web_summary

        finally:
            self.release_lock()

    def _is_naming_request(self, message: str) -> bool:
        """
        判断是否是Chatbox的对话命名请求

        Chatbox在每次对话后会自动发送一个命名请求，用于生成对话标题。
        该请求不应发送到ChatGPT网页，应直接本地生成名称返回，
        避免网页端出现用户在chatbox中未发送的额外提问。

        Args:
            message: 消息文本

        Returns:
            True表示是命名请求，False表示不是
        """
        naming_keywords = [
            "give this conversation a name",
            "Name this conversation",
            "conversation name",
            "name in 10 characters",
            "name is:",
        ]
        message_lower = message.lower().strip()
        for keyword in naming_keywords:
            if keyword.lower() in message_lower:
                return True
        return False

    def _generate_conversation_name(self, messages: list[ChatMessage]) -> str:
        """
        根据对话历史生成简短的对话名称

        优先使用从网页端提取的对话总结，如果没有则使用用户的原始提问。

        Args:
            messages: 消息列表

        Returns:
            对话名称（10字以内）
        """
        if self._conversation_summary:
            summary = self._conversation_summary.strip()
            if summary and summary != "对话" and summary != "新对话" and summary != "New chat":
                logger.info(f"Using extracted conversation summary: '{summary}'")
                return summary[:10]

        for msg in messages:
            if msg.role == "user":
                content = msg.get_text_content().strip()
                if content and not self._is_naming_request(content):
                    return content[:10]
        return "对话"

    async def _start_new_conversation(self) -> None:
        """
        在ChatGPT网页端点击新建对话按钮，确保每次请求都在干净的对话环境中进行

        ChatGPT网页有多种UI版本，需要尝试多种选择器来定位"新建对话"按钮：
        - 新版侧边栏的"+"按钮或"New chat"按钮
        - 旧版的"新对话"或"新建"按钮
        - 通过JavaScript遍历所有按钮寻找匹配的文本
        """
        try:
            new_chat_selectors = [
                'button[aria-label*="New chat"], button[aria-label*="新对话"], button[aria-label*="新建"]',
                'button:has-text("New chat"), button:has-text("新对话"), button:has-text("新建")',
                '[class*="new-chat"], [class*="new-conversation"], [data-testid*="new-chat"]',
                'button[class*="flex"][class*="gap-2"]',
                'button[data-ai-name="New Chat"]',
                'button[class*="p-3"], button[class*="w-10"], button[class*="h-10"]',
                'button[aria-label*="new"], button[aria-label*="create"], button[aria-label*="start"]',
                '[class*="sidebar"] button, [class*="aside"] button',
            ]

            for selector in new_chat_selectors:
                new_chat_button = await self.page.query_selector(selector)
                if new_chat_button:
                    try:
                        await new_chat_button.click()
                        logger.info(f"Clicked new chat button using selector: {selector}")
                        await asyncio.sleep(3)
                        return
                    except Exception:
                        logger.debug(f"Button click failed: {selector}")
                        continue

            logger.info("New chat button not found with CSS selectors, trying JavaScript search...")
            clicked = await self.page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = (btn.textContent || '').trim();
                        const ariaLabel = btn.getAttribute('aria-label') || '';
                        if (text.includes('New Chat') || text.includes('新对话') || text.includes('新建') || text.includes('+') || ariaLabel.includes('New chat') || ariaLabel.includes('新对话')) {
                            const isVisible = btn.offsetParent !== null;
                            const isEnabled = !btn.disabled;
                            if (isVisible && isEnabled) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                    
                    const sidebar = document.querySelector('[class*="sidebar"], [class*="aside"]');
                    if (sidebar) {
                        const sidebarButtons = sidebar.querySelectorAll('button');
                        for (const btn of sidebarButtons) {
                            const text = (btn.textContent || '').trim();
                            const ariaLabel = btn.getAttribute('aria-label') || '';
                            if (text.includes('+') || text.includes('New') || text.includes('新') || ariaLabel.includes('New') || ariaLabel.includes('new') || ariaLabel.includes('chat') || ariaLabel.includes('新')) {
                                const isVisible = btn.offsetParent !== null;
                                const isEnabled = !btn.disabled;
                                if (isVisible && isEnabled) {
                                    btn.click();
                                    return true;
                                }
                            }
                        }
                    }
                    
                    const svgs = document.querySelectorAll('svg');
                    for (const svg of svgs) {
                        const parentBtn = svg.closest('button');
                        if (parentBtn) {
                            const ariaLabel = parentBtn.getAttribute('aria-label') || '';
                            if (ariaLabel.includes('New chat') || ariaLabel.includes('新对话') || ariaLabel.includes('new')) {
                                const isVisible = parentBtn.offsetParent !== null;
                                const isEnabled = !parentBtn.disabled;
                                if (isVisible && isEnabled) {
                                    parentBtn.click();
                                    return true;
                                }
                            }
                        }
                    }
                    
                    return false;
                }
            """)

            if clicked:
                logger.info("Clicked new chat via JavaScript evaluation")
                await asyncio.sleep(3)
            else:
                logger.info("No new chat button found on ChatGPT page")

        except Exception as e:
            logger.error(f"Failed to start new conversation: {e}")

    async def _type_message(self, message: str) -> None:
        """
        在输入框中输入消息
        """
        logger.debug(f"Typing message: {message[:50]}...")

        try:
            input_elements = await self.page.query_selector_all(self.SELECTORS["input_box"])
            logger.debug(f"Found {len(input_elements)} input elements")

            chat_input_element = None
            for element in input_elements:
                placeholder = await self.page.evaluate("(el) => el.placeholder || ''", element)
                is_visible = await self.page.evaluate("(el) => el.offsetParent !== null", element)
                is_enabled = await self.page.evaluate("(el) => !el.disabled", element)
                
                logger.debug(f"Input element: placeholder='{placeholder}', visible={is_visible}, enabled={is_enabled}")
                
                if is_visible and is_enabled:
                    if 'Message' in placeholder or '消息' in placeholder or '输入' in placeholder:
                        chat_input_element = element
                        logger.debug(f"Selected chat input element with matching placeholder: '{placeholder}'")
                        break
                    elif not chat_input_element:
                        chat_input_element = element
                        logger.debug(f"Selected chat input element (no specific placeholder): '{placeholder}'")

            if chat_input_element:
                await chat_input_element.click()
                await asyncio.sleep(0.3)

                await self.page.keyboard.type(message, delay=100)
                await asyncio.sleep(0.5)

                current_value = await self.page.evaluate(
                    "(element) => element.value || element.innerText",
                    chat_input_element
                )
                logger.debug(f"Current input value: '{current_value}'")

                if current_value != message:
                    logger.warning(f"Input value mismatch: expected '{message}', got '{current_value}'")
                    await self.page.evaluate(
                        """([element, msg]) => {
                            element.value = msg;
                            element.dispatchEvent(new Event('input', { bubbles: true }));
                        }""",
                        [chat_input_element, message]
                    )
                    await asyncio.sleep(0.3)

                logger.debug("Message typed successfully")
            else:
                logger.error("No chat input box found")
                raise RuntimeError("Chat input box not found")

        except Exception as e:
            logger.error(f"Failed to type message: {e}")
            raise

    async def _press_send(self) -> None:
        """
        触发发送操作
        """
        logger.debug("Pressing send...")

        try:
            await self.page.click(self.SELECTORS["input_box"])
            await asyncio.sleep(0.5)

            await self.page.keyboard.press("Enter")
            logger.debug("Pressed Enter key")

            await asyncio.sleep(3)

            current_value = await self.page.evaluate("""
                () => {
                    const textarea = document.querySelector('textarea');
                    return textarea ? textarea.value : '';
                }
            """)
            logger.debug(f"Input value after send: '{current_value}'")

            await asyncio.sleep(2)

            messages = await self.page.query_selector_all(self.SELECTORS["chat_message"])
            logger.debug(f"Message count after send: {len(messages)}")

            self.last_message_count = len(messages)
            logger.info("Message sent successfully")

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise

    async def _extract_conversation_summary(self) -> str:
        """
        从ChatGPT网页端提取对话总结（标题）

        ChatGPT网页会自动为对话生成总结标题，通常显示在侧边栏或页面顶部。
        此方法尝试从多个位置提取对话总结：
        1. 侧边栏当前选中的对话标题
        2. 页面顶部的对话标题
        3. URL中的对话ID（作为备选）

        Returns:
            对话总结文本，如果未找到则返回空字符串
        """
        try:
            summary = await self.page.evaluate("""
                () => {
                    const selectors = [
                        '[data-testid*="conversation-title"]',
                        '[data-testid*="chat-title"]',
                        '.conversation-title',
                        '.chat-title',
                        '[class*="conversation-title"]',
                        '[class*="chat-title"]',
                        '[class*="active"] [class*="title"]',
                        '[class*="active"] [data-testid*="title"]',
                        '[data-testid="sidebar-item"]',
                        '[class*="sidebar"] [class*="active"]',
                        'h1',
                        'h2',
                        '[class*="heading"]',
                    ];
                    
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (el) {
                            const text = (el.textContent || '').trim();
                            if (text && text.length > 0 && text.length < 100) {
                                return text;
                            }
                        }
                    }
                    
                    const sidebarItems = document.querySelectorAll('[class*="sidebar"] [class*="item"], [class*="aside"] [class*="item"], [class*="menu"] [class*="item"]');
                    for (const item of sidebarItems) {
                        if (item.classList.contains('active') || item.classList.contains('selected')) {
                            const text = (item.textContent || '').trim();
                            if (text && text.length > 0 && text.length < 100) {
                                return text;
                            }
                        }
                    }
                    
                    return '';
                }
            """)

            if summary and summary.strip():
                logger.info(f"Extracted conversation summary from ChatGPT: '{summary}'")
                self._conversation_summary = summary.strip()
                return summary.strip()
            else:
                logger.debug("No conversation summary found on ChatGPT page")
                return ""
        except Exception as e:
            logger.debug(f"Failed to extract conversation summary: {e}")
            return ""

    async def _wait_for_conversation_summary(self, timeout: int = 15) -> str:
        """
        等待网页端生成对话总结，最多等待timeout秒

        Args:
            timeout: 最大等待时间（秒）

        Returns:
            对话总结文本，如果超时仍未找到则返回空字符串
        """
        logger.info(f"Waiting for conversation summary (timeout: {timeout}s)...")
        
        if self._conversation_summary and self._conversation_summary.strip():
            summary = self._conversation_summary.strip()
            if summary not in ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT"]:
                logger.info(f"Using existing conversation summary: '{summary}'")
                return summary
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            summary = await self._extract_web_summary()
            
            if summary and summary.strip() and summary.strip() not in ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT"]:
                self._conversation_summary = summary
                logger.info(f"Got conversation summary after {time.time() - start_time:.1f}s: '{summary}'")
                return summary
            
            await asyncio.sleep(0.5)
        
        logger.warning(f"Timeout waiting for conversation summary after {timeout}s")
        return ""

    async def _wait_for_reply(self) -> None:
        """
        等待AI回复完成
        """
        logger.debug("Waiting for reply...")

        try:
            start_time = time.time()

            while time.time() - start_time < self.max_wait_time:
                is_complete = await self._is_reply_complete()
                if is_complete:
                    logger.debug("Reply complete")
                    return

                await asyncio.sleep(1)

            logger.warning(f"Timeout waiting for reply after {self.max_wait_time} seconds")

        except Exception as e:
            logger.error(f"Error waiting for reply: {e}")

    async def _extract_web_summary(self) -> str:
        """
        根据当前页面URL提取网页端对话总结

        采用多层次提取策略，确保最大成功率：
        1. 优先提取侧边栏对话标题（如果存在）
        2. 提取最后一条AI回复作为总结
        3. 提取页面标题作为备选
        4. 使用JavaScript遍历DOM获取更多信息

        Returns:
            提取的对话总结文本，如果未找到则返回空字符串
        """
        try:
            current_url = self.page.url
            logger.info(f"Extracting web summary from URL: {current_url}")

            if "chat.deepseek.com" in current_url:
                return await self._extract_deepseek_summary()

            elif "chatgpt.com" in current_url:
                return await self._extract_chatgpt_summary()

            else:
                logger.warning(f"Unknown site: {current_url}, cannot extract summary")
                return ""

        except Exception as e:
            logger.error(f"Failed to extract web summary: {e}")
            return ""

    async def _extract_deepseek_summary(self) -> str:
        """
        提取DeepSeek网页端对话总结

        多层次提取策略：
        1. 侧边栏第一个对话项的标题
        2. 最后一条AI回复
        3. 页面标题
        """
        try:
            selectors = [
                '#root div.ds-scroll-area div.ds-scroll-area > div > div:nth-child(1) > a > div',
                '[class*="sidebar"] [class*="item"]:first-child',
                '[class*="conversation-list"] [class*="item"]:first-child',
                '[class*="ds-conversation"] [class*="title"]',
            ]

            for selector in selectors:
                try:
                    locator = self.page.locator(selector).first
                    await locator.wait_for(timeout=3000)
                    text = await locator.text_content()
                    if text and text.strip():
                        summary = text.strip()
                        logger.info(f"DeepSeek summary via selector '{selector}': '{summary}'")
                        return summary
                except Exception:
                    continue

            logger.info("DeepSeek sidebar selectors failed, trying AI reply extraction...")
            try:
                reply_locator = self.page.locator('div[role="article"]:last-child')
                await reply_locator.wait_for(timeout=5000)
                text = await reply_locator.text_content()
                if text and text.strip():
                    summary = text.strip()[:200]
                    logger.info(f"DeepSeek summary via AI reply: '{summary}'")
                    return summary
            except Exception:
                pass

            logger.info("DeepSeek AI reply extraction failed, trying page title...")
            try:
                title = await self.page.title()
                if title and title.strip():
                    summary = title.strip()
                    logger.info(f"DeepSeek summary via page title: '{summary}'")
                    return summary
            except Exception:
                pass

            logger.info("DeepSeek summary extraction failed, trying JavaScript evaluation...")
            summary = await self.page.evaluate("""
                () => {
                    const sidebarItems = document.querySelectorAll('[class*="sidebar"] [class*="item"], [class*="conversation"] [class*="item"]');
                    for (const item of sidebarItems) {
                        const titleEl = item.querySelector('[class*="title"], .ds-text, span');
                        if (titleEl) {
                            const text = titleEl.textContent.trim();
                            if (text && text.length > 0 && text.length < 200 && !text.includes('今天') && !text.includes('昨天')) {
                                return text;
                            }
                        }
                    }
                    
                    const articles = document.querySelectorAll('div[role="article"]');
                    if (articles.length > 0) {
                        const lastArticle = articles[articles.length - 1];
                        return lastArticle.textContent.trim().substring(0, 200);
                    }
                    
                    return '';
                }
            """)
            
            if summary and summary.strip():
                logger.info(f"DeepSeek summary via JS evaluation: '{summary}'")
                return summary.strip()

            logger.warning("DeepSeek summary extraction failed - all methods tried")
            return ""

        except Exception as e:
            logger.error(f"Failed to extract DeepSeek summary: {e}")
            return ""

    async def _extract_chatgpt_summary(self) -> str:
        """
        提取ChatGPT网页端对话总结

        多层次提取策略：
        1. 优先提取最后一条AI回复（更准确反映对话内容）
        2. 侧边栏当前对话标题（排除默认值）
        3. 页面标题
        4. JavaScript遍历DOM
        """
        try:
            ai_reply_selectors = [
                'div[data-message-author="assistant"]',
                '[class*="assistant"]',
                '[role="article"]',
                '[data-testid="message-content"]',
            ]

            for selector in ai_reply_selectors:
                try:
                    locator = self.page.locator(selector).last
                    await locator.wait_for(timeout=5000)
                    text = await locator.text_content()
                    if text and text.strip():
                        summary = text.strip()[:200]
                        logger.info(f"ChatGPT summary via AI reply selector '{selector}': '{summary}'")
                        return summary
                except Exception:
                    continue

            logger.info("ChatGPT AI reply extraction failed, trying sidebar title...")
            selectors = [
                '[data-testid*="conversation-title"]',
                '[data-testid*="chat-title"]',
                '.conversation-title',
                '.chat-title',
                '[class*="conversation-title"]',
                '[class*="chat-title"]',
                '[class*="active"] [class*="title"]',
                'h1',
                'h2',
            ]

            default_titles = ["历史聊天记录", "对话", "新对话", "New chat", "New Chat", "Untitled", "ChatGPT"]

            for selector in selectors:
                try:
                    locator = self.page.locator(selector).first
                    await locator.wait_for(timeout=3000)
                    text = await locator.text_content()
                    if text and text.strip():
                        summary = text.strip()
                        if summary not in default_titles:
                            logger.info(f"ChatGPT summary via selector '{selector}': '{summary}'")
                            return summary
                        else:
                            logger.info(f"ChatGPT skipped default title '{summary}'")
                except Exception:
                    continue

            logger.info("ChatGPT sidebar selectors failed, trying page title...")
            try:
                title = await self.page.title()
                if title and title.strip():
                    summary = title.strip()
                    summary = summary.replace(" - ChatGPT", "").replace("- ChatGPT", "")
                    if summary not in default_titles:
                        logger.info(f"ChatGPT summary via page title: '{summary}'")
                        return summary
            except Exception:
                pass

            logger.info("ChatGPT summary extraction failed, trying JavaScript evaluation...")
            summary = await self.page.evaluate("""
                () => {
                    const defaultTitles = ["历史聊天记录", "对话", "新对话", "New chat", "New Chat", "Untitled", "ChatGPT"];
                    
                    const aiReplySelectors = ['div[data-message-author="assistant"]', '[class*="assistant"]', '[role="article"]', '[data-testid="message-content"]'];
                    for (const selector of aiReplySelectors) {
                        const els = document.querySelectorAll(selector);
                        if (els.length > 0) {
                            const lastEl = els[els.length - 1];
                            const text = lastEl.textContent.trim();
                            if (text && text.length > 0 && text.length < 300) {
                                return text.substring(0, 200);
                            }
                        }
                    }
                    
                    const titleSelectors = ['[data-testid*="conversation-title"]', '[data-testid*="chat-title"]', '.conversation-title', '.chat-title', '[class*="conversation-title"]', '[class*="chat-title"]', '[class*="active"] [class*="title"]', 'h1', 'h2'];
                    for (const selector of titleSelectors) {
                        const el = document.querySelector(selector);
                        if (el) {
                            const text = el.textContent.trim();
                            if (text && text.length > 0 && text.length < 100 && !defaultTitles.includes(text)) {
                                return text;
                            }
                        }
                    }
                    
                    return '';
                }
            """)
            
            if summary and summary.strip():
                logger.info(f"ChatGPT summary via JS evaluation: '{summary}'")
                return summary.strip()

            logger.warning("ChatGPT summary extraction failed - all methods tried")
            return ""

        except Exception as e:
            logger.error(f"Failed to extract ChatGPT summary: {e}")
            return ""

    async def _push_summary_to_chatbox(self, web_summary: str) -> bool:
        """
        将对话总结推送到Chatbox API

        Args:
            web_summary: 从网页端提取的对话总结

        Returns:
            True表示推送成功，False表示失败
        """
        if not web_summary or not web_summary.strip():
            logger.info("未读取到网页总结，跳过推送")
            return False

        try:
            import httpx
            import json
            from datetime import datetime

            conversation_id = str(uuid.uuid4())
            
            sanitized_summary = web_summary.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')

            payload = {
                "id": conversation_id,
                "title": "对话总结",
                "summary": sanitized_summary,
                "messages": [],
                "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            }

            headers = {
                "Authorization": "Bearer sk-1234",
                "Content-Type": "application/json",
            }

            logger.info(f"Preparing to push summary to Chatbox, length={len(sanitized_summary)}, first_50_chars='{sanitized_summary[:50]}...'")

            async with httpx.AsyncClient(timeout=10.0) as client:
                for attempt in range(3):
                    try:
                        response = await client.post(
                            "http://localhost:8000/v1/conversations",
                            headers=headers,
                            json=payload,
                        )
                        if response.status_code == 200:
                            logger.info(f"对话总结已同步至Chatbox会话列表: '{sanitized_summary[:30]}...'")
                            return True
                        else:
                            logger.warning(
                                f"Chatbox API request failed (attempt {attempt + 1}): status={response.status_code}, body={response.text[:200]}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Chatbox API request error (attempt {attempt + 1}): {e}"
                        )
                    
                    if attempt < 2:
                        await asyncio.sleep(1)

            logger.error(f"Chatbox API request failed after 3 attempts")
            return False

        except Exception as e:
            logger.error(f"Failed to push summary to Chatbox: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    async def _is_reply_complete(self) -> bool:
        """
        判断AI回复是否完成
        """
        try:
            loading_elements = await self.page.query_selector_all(
                self.SELECTORS["loading_indicator"]
            )
            if loading_elements:
                return False

            stop_button = await self.page.query_selector(self.SELECTORS["stop_button"])
            if stop_button:
                return False

            current_text = await self._extract_reply_content()
            if current_text == self.last_reply_text:
                self.stable_count += 1
                if self.stable_count >= 5:
                    return True
            else:
                self.stable_count = 0
                self.last_reply_text = current_text

            return False

        except Exception as e:
            logger.debug(f"Error checking reply completion: {e}")
            return False

    async def _extract_reply_content(self, include_images: bool = True) -> str:
        """
        提取AI回复的正文内容

        通过JavaScript精确定位DOM元素，清理后的HTML通过markdownify转换（保留格式），
        图片URL单独收集并在Python端追加。
        """
        logger.info("EXTRACT_V7_MARKDOWNIFY_MODE: Starting content extraction")
        try:
            await self.page.evaluate("""
                const scrollContainer = document.querySelector('[class*="main"], [class*="conversation"], [class*="chat-container"]') || 
                                       document.querySelector('[style*="overflow"], [style*="scroll"]') ||
                                       document.body;
                scrollContainer.scrollTop = scrollContainer.scrollHeight;
            """)
            await asyncio.sleep(3)
            await self.page.evaluate("""
                const scrollContainer = document.querySelector('[class*="main"], [class*="conversation"], [class*="chat-container"]') || 
                                       document.querySelector('[style*="overflow"], [style*="scroll"]') ||
                                       document.body;
                scrollContainer.scrollTop = 0;
            """)
            
            js_code = """
                () => {
                    const debugInfo = [];
                    
                    const sidebar = document.querySelector('[class*="sidebar"], [class*="history"], nav');
                    
                    const allMessages = document.querySelectorAll('[data-message-id], article, [class*="message"]');
                    debugInfo.push('Total message elements: ' + allMessages.length);
                    
                    const allHtmlContents = [];
                    const allImgUrls = [];
                    const allCodeBlocks = [];
                    let foundAssistant = false;
                    
                    for (let i = allMessages.length - 1; i >= 0; i--) {
                        const msg = allMessages[i];
                        
                        if (sidebar && sidebar.contains(msg)) {
                            continue;
                        }
                        
                        const hasAssistantRole = msg.getAttribute('data-author-role') === 'assistant';
                        const hasAssistantClass = msg.querySelector('[class*="assistant"], .gpt-message') !== null;
                        const hasMarkdown = msg.querySelector('.markdown, [class*="markdown"]');
                        const isUser = msg.querySelector('[class*="user"]') !== null;
                        
                        const isAssistant = (hasAssistantRole && !isUser) || hasAssistantClass || (hasMarkdown && !isUser);
                        
                        if (isAssistant) {
                            foundAssistant = true;
                            const content = msg.querySelector('.markdown, [class*="markdown"], [class*="content"], .prose');
                            if (content) {
                                const imgs = content.querySelectorAll('img, picture img');
                                imgs.forEach(img => {
                                    const src = img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
                                    if (src && !src.startsWith('data:')) {
                                        allImgUrls.push(src);
                                    }
                                });
                                
                                const clone = content.cloneNode(true);
                                clone.querySelectorAll('img, picture img').forEach(el => el.remove());
                                
                                let codeBlockIndex = 0;
                                clone.querySelectorAll('pre').forEach(pre => {
                                    if (pre.querySelector('table')) {
                                        return;
                                    }
                                    
                                    const preClone = pre.cloneNode(true);
                                    preClone.querySelectorAll('button, [role="button"], .copy-btn, .run-btn, span:has(button), [class*="toolbar"], [class*="header"]').forEach(el => el.remove());
                                    
                                    let lang = '';
                                    const codeEl = preClone.querySelector('code');
                                    if (codeEl) {
                                        const codeClass = codeEl.getAttribute('class') || '';
                                        const langMatch = codeClass.match(/language-(\\w+)/);
                                        if (langMatch) {
                                            lang = langMatch[1].toLowerCase();
                                        }
                                    }
                                    if (!lang) {
                                        const preClass = preClone.getAttribute('class') || '';
                                        const langMatch = preClass.match(/language-(\\w+)/);
                                        if (langMatch) {
                                            lang = langMatch[1].toLowerCase();
                                        }
                                    }
                                    if (!lang) {
                                        const parent = pre.parentElement;
                                        if (parent) {
                                            const langLabels = parent.querySelectorAll('[class*="lang"], [class*="language"], [class*="code-header"], [class*="code-lang"]');
                                            langLabels.forEach(label => {
                                                const text = label.textContent.trim();
                                                const normalized = text.toLowerCase();
                                                if (normalized.includes('python')) lang = 'python';
                                                else if (normalized.includes('javascript') || normalized.includes('js')) lang = 'javascript';
                                                else if (normalized.includes('java')) lang = 'java';
                                                else if (normalized.includes('c++') || normalized.includes('cpp')) lang = 'cpp';
                                                else if (normalized.includes('c#')) lang = 'csharp';
                                                else if (normalized.includes('c ') && !normalized.includes('c++')) lang = 'c';
                                                else if (normalized.includes('go')) lang = 'go';
                                                else if (normalized.includes('sql')) lang = 'sql';
                                                else if (normalized.includes('html')) lang = 'html';
                                                else if (normalized.includes('css')) lang = 'css';
                                                else if (normalized.includes('json')) lang = 'json';
                                                else if (normalized.includes('typescript') || normalized.includes('ts')) lang = 'typescript';
                                                else if (normalized.includes('rust')) lang = 'rust';
                                                else if (normalized.includes('bash') || normalized.includes('shell')) lang = 'bash';
                                                else if (normalized.includes('yaml') || normalized.includes('yml')) lang = 'yaml';
                                            });
                                        }
                                    }
                                    if (!lang) {
                                        const preText = preClone.textContent.toLowerCase();
                                        if (preText.includes('def ') || preText.includes('print(') || preText.includes('import ') || preText.includes('from ') || preText.includes('# ') || preText.includes('python')) {
                                            lang = 'python';
                                        } else if (preText.includes('function ') || preText.includes('const ') || preText.includes('let ') || preText.includes('console.log(') || preText.includes('=>')) {
                                            lang = 'javascript';
                                        } else if (preText.includes('public static void') || preText.includes('system.out.println') || preText.includes('import java.')) {
                                            lang = 'java';
                                        } else if (preText.includes('select ') || preText.includes('from ') || preText.includes('where ') || preText.includes('insert ') || preText.includes('update ') || preText.includes('delete ')) {
                                            lang = 'sql';
                                        } else if (preText.includes('package main') || preText.includes('func ') || preText.includes('import "') || preText.includes('go.')) {
                                            lang = 'go';
                                        } else if (preText.includes('#include') || preText.includes('using namespace') || preText.includes('int main')) {
                                            if (preText.includes('cpp') || preText.includes('c++')) lang = 'cpp';
                                            else if (preText.includes('c#') || preText.includes('using System')) lang = 'csharp';
                                            else lang = 'cpp';
                                        } else if (preText.includes('<html') || preText.includes('<body')) {
                                            lang = 'html';
                                        } else if (preText.includes('@media') || preText.includes('color:')) {
                                            lang = 'css';
                                        } else if (preText.includes('{') && preText.includes('}') && (preText.includes(':') || preText.includes(','))) {
                                            lang = 'json';
                                        }
                                    }
                                    
                                    const codeText = preClone.textContent.trim();
                                    
                                    const codeBlock = '```' + lang + '\\n' + codeText + '\\n```';
                                    allCodeBlocks.push(codeBlock);
                                    
                                    const placeholder = document.createElement('p');
                                    placeholder.setAttribute('data-cb', codeBlockIndex.toString());
                                    placeholder.textContent = 'CODEBLOCKPLACEHOLDER' + codeBlockIndex + 'END';
                                    pre.replaceWith(placeholder);
                                    codeBlockIndex++;
                                });
                                
                                clone.querySelectorAll('*').forEach(el => {
                                    while(el.attributes.length > 0) {
                                        const attr = el.attributes[0];
                                        if (attr.name === 'href' && attr.value) {
                                            const href = attr.value;
                                            if (href.includes('yahoo.com') || href.includes('google.com') || 
                                                href.includes('bing.com') || href.includes('wikipedia.org') ||
                                                href.includes('kids.yahoo')) {
                                                el.remove();
                                                return;
                                            }
                                        }
                                        el.removeAttribute(attr.name);
                                    }
                                });
                                clone.querySelectorAll('*').forEach(el => {
                                    const text = el.textContent.trim();
                                    if (text.match(/^\\d{1,3}$/) && el.children.length === 0) {
                                        el.remove();
                                    } else if (text.includes('http://') || text.includes('https://')) {
                                        el.remove();
                                    }
                                });
                                
                                const html = clone.innerHTML.trim();
                                if (html.length > 0) {
                                    const isDuplicate = allHtmlContents.some(existing => existing === html || 
                                        (existing.length === html.length && existing.substring(0, 200) === html.substring(0, 200)));
                                    if (!isDuplicate) {
                                        allHtmlContents.push(html);
                                        debugInfo.push('Assistant HTML block found, length: ' + html.length);
                                    }
                                }
                            }
                        } else if (foundAssistant) {
                            break;
                        }
                    }
                    
                    debugInfo.push('Total image URLs: ' + allImgUrls.length);
                    debugInfo.push('Total HTML blocks: ' + allHtmlContents.length);
                    debugInfo.push('Total code blocks: ' + allCodeBlocks.length);
                    
                    if (allHtmlContents.length > 0) {
                        const fullHtml = allHtmlContents.join('<hr>');
                        debugInfo.push('Full HTML length: ' + fullHtml.length);
                        debugInfo.push('Full HTML preview: ' + fullHtml.substring(0, 500));
                        
                        return { found: true, html: fullHtml, imgUrls: allImgUrls, codeBlocks: allCodeBlocks, debug: debugInfo.join(' | ') };
                    }
                    
                    return { found: false, html: '', imgUrls: [], codeBlocks: [], debug: debugInfo.join(' | ') || 'No valid content found' };
                }
            """
            
            result = await self.page.evaluate(js_code)
            
            logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: Extraction result found={result.get('found', False)}")
            
            if result.get('found', False):
                logger.debug(f"Content extraction debug: {result.get('debug', '')}")
                html = result.get('html', '')
                img_urls = result.get('imgUrls', [])
                code_blocks = result.get('codeBlocks', [])
                
                logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: Raw HTML length={len(html)}, image URLs={len(img_urls)}, code blocks={len(code_blocks)}")
                logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: Raw HTML preview: {html[:500]}")
                
                try:
                    from markdownify import markdownify as html_to_markdown
                    markdown = html_to_markdown(html, heading_style="ATX")
                    logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: HTML converted to Markdown, length: {len(markdown)}")
                    logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: Markdown preview: {markdown[:500]}")
                except Exception as e:
                    logger.warning(f"EXTRACT_V7_MARKDOWNIFY_MODE: Failed to convert HTML to Markdown: {e}, using plain text")
                    import re
                    markdown = re.sub(r'<[^>]+>', '', html)
                
                for i, code_block in enumerate(code_blocks):
                    markdown = markdown.replace(f'CODEBLOCKPLACEHOLDER{i}END', code_block)
                
                import re
                lines = markdown.split('\n')
                cleaned_lines = []
                for line in lines:
                    stripped = line.strip()
                    if stripped not in ['复制', '下载', 'text', 'Copy code', 'Download']:
                        cleaned_lines.append(line)
                markdown = '\n'.join(cleaned_lines)
                markdown = re.sub(r'\n{3,}', '\n\n', markdown)
                markdown = markdown.strip()
                
                if img_urls:
                    seen_urls = set()
                    unique_urls = []
                    for url in img_urls:
                        if 'blob:' not in url and 'http' in url:
                            is_thumbnail = ('sz=128' in url or 'w=128' in url or 'h=128' in url or 
                                           'sz=64' in url or 'w=64' in url or 'h=64' in url or
                                           'favicon' in url.lower())
                            if not is_thumbnail:
                                if url not in seen_urls:
                                    seen_urls.add(url)
                                    unique_urls.append(url)
                    
                    if include_images:
                        logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: Appending {len(unique_urls)} unique image URLs")
                        for url in unique_urls:
                            markdown += f'\n\n![]({url})'
                
                logger.info(f"EXTRACT_V7_MARKDOWNIFY_MODE: Final content length={len(markdown)}")
                return markdown
            else:
                logger.debug(f"Content extraction debug: {result.get('debug', '')}")
                return ""

        except Exception as e:
            logger.error(f"EXTRACT_V7_MARKDOWNIFY_MODE: Failed to extract reply content: {e}")
            return ""

    async def cleanup(self) -> None:
        """
        清理资源，关闭浏览器和页面
        """
        logger.info("Cleaning up ChatGPT adapter resources...")
        try:
            if self.page:
                await self.page.close()
                self.page = None
            if self.context:
                await self.context.close()
                self.context = None
            if self.browser:
                await self.browser.close()
                self.browser = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            logger.info("ChatGPT adapter cleaned up successfully")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")