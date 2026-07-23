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

EDGE_DEFAULT_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Users\{username}\AppData\Local\Microsoft\Edge\Application\msedge.exe",
]


class DeepseekAdapter(BaseAIAdapter):
    """
    Deepseek Web AI服务适配器

    实现对Deepseek网页的自动化操作，包括：
    - 浏览器初始化与生命周期管理
    - 输入框定位与消息发送
    - 回复状态判断与内容提取
    - 错误处理与重试机制
    """

    Deepseek_URL = "https://chat.deepseek.com/"
    MODEL_NAME = "K2.6"

    SELECTORS = {
        "input_box": "textarea[placeholder*='发送消息'], textarea[placeholder*='发送'], textarea[placeholder*='输入'], textarea[placeholder*='Message'], textarea[placeholder*='message'], textarea[class*='input'], textarea[id*='input'], [role='textbox'], div[contenteditable='true'], .ProseMirror, ._27c9245, .d96f2d2a",
        "send_button": "button[aria-label*='发送'], button:has(svg), button[type='submit'], [class*='send'], [aria-label*='Send'], button[data-testid*='send'], button[class*='flex']",
        "chat_message": "[data-role='message'], [class*='message'], [data-id*='message'], li[class*='message'], [data-testid*='message'], .ds-message",
        "assistant_message": "[data-role='message'][data-author='assistant'], [class*='assistant'], [data-author='assistant'], [class*='response'], [class*='bot'], [data-role='assistant'], div[data-author='assistant'], [data-testid*='assistant'], [class*='role-assistant'], article[data-testid*='message'], .ds-assistant-message",
        "message_content": ".message-content, .markdown-body, [class*='content'], .prose, .markdown, [class*='markdown'], [class*='text-content'], [class*='message-text'], [data-testid*='text'], [class*='whitespace-pre-wrap'], .ds-markdown, .ds-assistant-message-main-content",
        "loading_indicator": ".loading, [class*='loading'], [aria-busy='true'], [class*='spin'], [class*='typing'], [data-testid*='typing'], .ds-loading",
        "stop_button": "button[aria-label*='停止'], button:has-text('停止'), [class*='stop'], [aria-label*='Stop'], button[data-testid*='stop']",
        "model_selector": "[data-testid='model-selector'], select, [class*='model-select']",
    }

    def __init__(self, config: AdapterConfig = None):
        if config is None:
            config = AdapterConfig(headless=False)
        super().__init__(config)
        self.last_message_count = 0
        self.last_reply_text = ""
        self.stable_count = 0
        self.max_wait_time = config.timeout
        self._conversation_summary = ""

    @staticmethod
    def _find_edge_executable() -> Optional[str]:
        """
        查找系统中已安装的Edge浏览器可执行文件路径

        Returns:
            Edge浏览器可执行文件路径，如果未找到则返回None
        """
        username = os.environ.get("USERNAME", "")

        for path in EDGE_DEFAULT_PATHS:
            expanded_path = path.format(username=username)
            if os.path.exists(expanded_path):
                logger.info(f"Found Edge browser at: {expanded_path}")
                return expanded_path

        return None

    async def initialize(self) -> None:
        """
        初始化Deepseek服务适配器

        启动浏览器，创建上下文，导航到Deepseek网页，
        等待页面加载完成并验证登录状态。

        支持使用系统已安装的Edge浏览器替代Chromium：
        1. 通过channel参数指定"msedge"自动检测
        2. 通过executable_path参数指定具体路径
        3. 自动查找系统中已安装的Edge浏览器
        """
        logger.info("Initializing Deepseek adapter...")

        try:
            self.playwright = await async_playwright().start()

            launch_options = {
                "headless": self.config.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1280,720",
                ],
            }

            if self.config.user_data_dir:
                launch_options["user_data_dir"] = self.config.user_data_dir

            browser_launcher = self.playwright.chromium

            if self.config.channel:
                launch_options["channel"] = self.config.channel
                logger.info(f"Using browser channel: {self.config.channel}")
            elif self.config.executable_path:
                launch_options["executable_path"] = self.config.executable_path
                logger.info(f"Using browser executable: {self.config.executable_path}")
            else:
                edge_path = self._find_edge_executable()
                if edge_path:
                    launch_options["executable_path"] = edge_path
                    logger.info("Using system-installed Microsoft Edge browser")
                else:
                    logger.info("Using default Playwright Chromium browser")

            self.browser = await browser_launcher.launch(**launch_options)
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            self.page = await self.context.new_page()

            await self.page.goto(self.Deepseek_URL, timeout=self.config.page_load_timeout * 1000)

            await self._wait_for_page_ready()

            await self._select_model(self.MODEL_NAME)

            logger.info("Deepseek adapter initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Deepseek adapter: {e}")
            await self.cleanup()
            raise

    async def cleanup(self) -> None:
        """
        清理资源，关闭浏览器和页面

        按照正确的顺序释放资源：page -> context -> browser -> playwright
        """
        logger.info("Cleaning up Deepseek adapter...")

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
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    async def send_message(self, messages: list[ChatMessage]) -> str:
        """
        发送消息并获取AI回复

        Args:
            messages: 消息列表，包含对话历史

        Returns:
            AI回复的文本内容
        """
        await self.acquire_lock()
        try:
            user_message = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    user_message = msg.get_text_content()
                    break

            if not user_message:
                logger.warning("No user message found, using last message")
                user_message = messages[-1].get_text_content() if messages else ""

            logger.debug(f"Selected user message to send: '{user_message[:50]}'...")

            if self._is_naming_request(user_message):
                await self._wait_for_conversation_summary(timeout=15)
                name = self._generate_conversation_name(messages)
                logger.info(f"Intercepted naming request, returning: '{name}'")
                return name

            await self._ensure_input_ready()

            user_msg_count = sum(1 for msg in messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
            if user_msg_count == 1:
                await self._start_new_conversation()

            await self._type_message(user_message)

            await self._press_send()

            reply_text = await self._wait_for_reply()

            await asyncio.sleep(2)
            await self._extract_conversation_summary()

            return reply_text.strip()

        finally:
            self.release_lock()

    def _is_naming_request(self, message: str) -> bool:
        """
        判断是否是Chatbox的对话命名请求

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
            if summary and summary != "对话" and summary != "新对话":
                summary = summary.replace(" - DeepSeek", "").replace("- DeepSeek", "")
                summary = summary.replace(" - ChatGPT", "").replace("- ChatGPT", "")
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
        在DeepSeek网页端点击新建对话按钮，确保每次请求都在干净的对话环境中进行

        DeepSeek网页有多种UI版本，需要尝试多种选择器来定位"新建对话"按钮：
        - 侧边栏的"+"按钮或"开启新对话"/"新对话"/"New Chat"按钮
        - 通过JavaScript遍历所有链接寻找匹配的文本（DeepSeek使用<a>标签而非<button>标签）
        """
        try:
            logger.info("Searching for new chat button on DeepSeek page...")
            
            debug_info = await self.page.evaluate("""
                () => {
                    const result = {
                        buttons: [],
                        links: [],
                        allElements: [],
                    };
                    
                    document.querySelectorAll('button').forEach((btn, i) => {
                        const text = (btn.textContent || '').trim();
                        const ariaLabel = btn.getAttribute('aria-label') || '';
                        result.buttons.push({
                            index: i,
                            text: text.substring(0, 50),
                            ariaLabel: ariaLabel.substring(0, 50),
                            tag: 'button',
                        });
                    });
                    
                    document.querySelectorAll('a').forEach((link, i) => {
                        const text = (link.textContent || '').trim();
                        const ariaLabel = link.getAttribute('aria-label') || '';
                        const className = link.className || '';
                        if (text.length > 0 || ariaLabel.length > 0) {
                            result.links.push({
                                index: i,
                                text: text.substring(0, 50),
                                ariaLabel: ariaLabel.substring(0, 50),
                                className: className.substring(0, 100),
                                tag: 'a',
                            });
                        }
                    });
                    
                    const clickableSelectors = ['[role="button"]', '.btn', '.button', '[class*="btn"]', '[class*="button"]', '[class*="click"]'];
                    clickableSelectors.forEach((selector, idx) => {
                        document.querySelectorAll(selector).forEach((el, i) => {
                            const text = (el.textContent || '').trim();
                            if (text.length > 0 && !result.allElements.find(e => e.text === text)) {
                                result.allElements.push({
                                    index: i,
                                    text: text.substring(0, 50),
                                    selector: selector,
                                    tag: el.tagName.toLowerCase(),
                                });
                            }
                        });
                    });
                    
                    return result;
                }
            """)
            
            logger.info(f"Debug info - Buttons found: {len(debug_info['buttons'])}")
            logger.info(f"Debug info - Links found: {len(debug_info['links'])}")
            logger.info(f"Debug info - Clickable elements: {len(debug_info['allElements'])}")
            
            for link in debug_info['links'][:20]:
                if '开启' in link['text'] or '新' in link['text'] or 'new' in link['text'].lower() or 'chat' in link['text'].lower():
                    logger.info(f"Potential new chat link: text='{link['text']}', ariaLabel='{link['ariaLabel']}', className='{link['className']}'")
            
            for el in debug_info['allElements'][:10]:
                logger.debug(f"Clickable element: text='{el['text']}', selector='{el['selector']}', tag='{el['tag']}'")

            new_chat_selectors = [
                'a:has-text("开启新对话"), a:has-text("新对话"), a:has-text("新建"), a:has-text("New"), a:has-text("New Chat")',
                'a[aria-label*="New chat"], a[aria-label*="新对话"], a[aria-label*="新建"], a[aria-label*="开启"]',
                '[role="button"]:has-text("开启"), [role="button"]:has-text("新"), [role="button"]:has-text("New")',
                '[class*="new-chat"], [class*="new-conversation"], [data-testid*="new-chat"]',
                '.btn:has-text("开启"), .btn:has-text("新"), .button:has-text("开启"), .button:has-text("新")',
            ]

            for selector in new_chat_selectors:
                new_chat_element = await self.page.query_selector(selector)
                if new_chat_element:
                    try:
                        is_visible = await self.page.evaluate("(el) => el.offsetParent !== null", new_chat_element)
                        if is_visible:
                            await new_chat_element.click()
                            logger.info(f"Clicked new chat using selector: {selector}")
                            await asyncio.sleep(3)
                            return
                        else:
                            logger.debug(f"Element found but not visible: {selector}")
                    except Exception:
                        logger.debug(f"Element not clickable: {selector}")
                        continue

            logger.info("New chat not found with CSS selectors, trying JavaScript search on links...")
            clicked = await self.page.evaluate("""
                () => {
                    const links = document.querySelectorAll('a');
                    for (const link of links) {
                        const text = (link.textContent || '').trim();
                        const ariaLabel = link.getAttribute('aria-label') || '';
                        const className = link.className || '';
                        const isVisible = link.offsetParent !== null;
                        if (isVisible && (text.includes('开启新对话') || text.includes('新对话') || text.includes('新建') || text.includes('New Chat') || text.includes('New') || text.includes('开启') || ariaLabel.includes('new') || ariaLabel.includes('chat') || ariaLabel.includes('新') || ariaLabel.includes('开启'))) {
                            link.click();
                            return true;
                        }
                    }
                    
                    const buttonRoles = document.querySelectorAll('[role="button"]');
                    for (const btn of buttonRoles) {
                        const text = (btn.textContent || '').trim();
                        const isVisible = btn.offsetParent !== null;
                        if (isVisible && (text.includes('开启新对话') || text.includes('新对话') || text.includes('新建') || text.includes('New Chat') || text.includes('New') || text.includes('开启'))) {
                            btn.click();
                            return true;
                        }
                    }
                    
                    const allElements = document.querySelectorAll('*');
                    for (const el of allElements) {
                        const text = (el.textContent || '').trim();
                        const className = el.className || '';
                        if (text === '开启新对话' || text === '新对话') {
                            const isVisible = el.offsetParent !== null;
                            const isClickable = window.getComputedStyle(el).cursor === 'pointer';
                            if (isVisible && isClickable) {
                                el.click();
                                return true;
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
                logger.info("No new chat button found on DeepSeek page")

        except Exception as e:
            logger.error(f"Failed to start new conversation: {e}")

    async def send_message_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        """
        发送消息并以流式方式获取AI回复

        Args:
            messages: 消息列表，包含对话历史

        Yields:
            流式回复的文本片段
        """
        await self.acquire_lock()
        try:
            user_message = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    user_message = msg.get_text_content()
                    break

            if not user_message:
                user_message = messages[-1].get_text_content() if messages else ""

            logger.debug(f"Selected user message to send (stream): '{user_message[:50]}'...")

            if self._is_naming_request(user_message):
                name = self._generate_conversation_name(messages)
                logger.info(f"Intercepted naming request (stream), returning: '{name}'")
                yield name
                return

            await self._ensure_input_ready()

            user_msg_count = sum(1 for msg in messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
            if user_msg_count == 1:
                await self._start_new_conversation()

            await self._type_message(user_message)

            await self._press_send()

            async for chunk in self._stream_reply():
                yield chunk

            await asyncio.sleep(3)
            web_summary = await self._extract_web_summary()
            self._conversation_summary = web_summary

        finally:
            self.release_lock()

    async def _wait_for_page_ready(self) -> None:
        """
        等待页面加载完成，包括：
        - 页面基础元素加载
        - 登录状态判断（如有）
        - 输入框可见且可交互
        """
        logger.debug("Waiting for page ready...")

        try:
            await self.page.wait_for_load_state("networkidle", timeout=30000)
            logger.debug("Network idle state reached")

            await asyncio.sleep(2)

            page_title = await self.page.title()
            logger.debug(f"Page title: {page_title}")

            current_url = self.page.url
            logger.debug(f"Current URL: {current_url}")

            await self._handle_possible_login()

            await self._select_chat_mode()

            all_textareas = await self.page.query_selector_all('textarea')
            logger.debug(f"Found {len(all_textareas)} textarea elements")
            
            if all_textareas:
                textarea_info = await self.page.evaluate(
                    "(el) => ({ placeholder: el.placeholder, className: el.className, type: el.type, id: el.id })",
                    all_textareas[0]
                )
                logger.debug(f"First textarea info: {textarea_info}")

            all_inputs = await self.page.query_selector_all('input')
            logger.debug(f"Found {len(all_inputs)} input elements")
            
            if all_inputs:
                input_info = await self.page.evaluate(
                    "(el) => ({ placeholder: el.placeholder, className: el.className, type: el.type, id: el.id })",
                    all_inputs[0]
                )
                logger.debug(f"First input info: {input_info}")

            contenteditable_divs = await self.page.query_selector_all('div[contenteditable="true"]')
            logger.debug(f"Found {len(contenteditable_divs)} contenteditable divs")

            await self.page.wait_for_selector(
                self.SELECTORS["input_box"],
                timeout=self.config.page_load_timeout * 1000,
            )
            logger.debug("Input box found")

        except PlaywrightTimeoutError:
            logger.error("Timeout waiting for page ready")
            raise

    async def _select_chat_mode(self) -> None:
        """
        自动选择聊天模式

        DeepSeek需要先选择对话模式（快速模式、专家模式、识图模式），
        这里自动选择快速模式以便立即开始聊天。
        """
        try:
            mode_selected = await self.page.evaluate("""
                () => {
                    const inputBox = document.querySelector('textarea[placeholder*="发送"], textarea[placeholder*="输入"], [role="textbox"], div[contenteditable="true"]');
                    if (inputBox) {
                        const isVisible = inputBox.offsetParent !== null;
                        if (isVisible) {
                            return { selected: true, reason: 'input box already visible, no mode selection needed' };
                        }
                    }
                    
                    const allButtons = document.querySelectorAll('button');
                    for (const btn of allButtons) {
                        const text = btn.textContent || '';
                        if (text.includes('快速模式') || text.includes('Quick')) {
                            btn.click();
                            return { selected: true, reason: 'selected quick mode via button' };
                        }
                    }
                    
                    const modeCards = document.querySelectorAll('.ds-chat-mode-card');
                    if (modeCards.length > 0) {
                        modeCards[0].click();
                        return { selected: true, reason: 'selected first mode card' };
                    }
                    
                    return { selected: false, reason: 'no mode selection found' };
                }
            """)
            
            logger.debug(f"Chat mode selection: {mode_selected}")
            
            if mode_selected.get('selected'):
                await asyncio.sleep(3)
                logger.info("Chat mode selected, continuing...")

        except Exception as e:
            logger.warning(f"Failed to select chat mode: {e}")

    async def _handle_possible_login(self) -> None:
        """
        处理可能的登录状态

        检查页面是否存在登录相关元素，如二维码、登录按钮等，
        如果存在，尝试自动登录或等待用户手动完成登录。
        """
        try:
            current_url = self.page.url
            login_selector = "button:has-text('登录'), button:has-text('Sign in'), [class*='login'], button[type='submit']"
            qr_selector = "img[src*='qrcode'], [class*='qrcode'], [id*='qrcode']"
            email_input = await self.page.query_selector('input[placeholder*="手机号"], input[placeholder*="邮箱"], input[placeholder*="email"]')

            login_element = await self.page.query_selector(login_selector)
            qr_element = await self.page.query_selector(qr_selector)

            is_login_page = current_url.lower().find('sign_in') != -1 or \
                          current_url.lower().find('login') != -1 or \
                          current_url.lower().find('signin') != -1

            if login_element or qr_element or email_input or is_login_page:
                logger.info("Login page detected, attempting auto-login...")

                await self._auto_login()

                await asyncio.sleep(5)
                new_url = self.page.url
                logger.info(f"After login URL: {new_url}")

                if new_url.lower().find('sign_in') != -1 or new_url.lower().find('login') != -1:
                    logger.warning("Login may not have succeeded, URL still on login page")
                else:
                    logger.info("Login completed successfully, URL changed")

                await self.page.wait_for_load_state("networkidle", timeout=30000)
                logger.debug("Network idle after login")

        except Exception as e:
            logger.warning(f"Error handling login: {e}")

    async def _auto_login(self) -> None:
        """
        自动登录DeepSeek

        使用提供的手机号和密码自动完成登录流程：
        1. 点击"密码登录"链接切换到密码登录模式
        2. 输入手机号
        3. 输入密码
        4. 点击登录按钮
        5. 等待页面跳转到聊天界面
        """
        try:
            phone = os.environ.get("Deepseek_PHONE", "")
            password = os.environ.get("Deepseek_PASSWORD", "")

            await asyncio.sleep(2)

            password_login_link = await self.page.query_selector('.ds-sign-in-form__social-link')
            if password_login_link:
                logger.info("Found password login button via class selector, clicking...")
                await password_login_link.click()
                await asyncio.sleep(3)
            else:
                logger.debug("Password login button not found with class selector, trying text search...")
                success = await self.page.evaluate("""
                    () => {
                        const elements = document.querySelectorAll('.ds-button');
                        for (const el of elements) {
                            const text = el.textContent || '';
                            if (text.trim() === '密码登录') {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                if success:
                    logger.info("Clicked password login via JavaScript search")
                    await asyncio.sleep(3)
                else:
                    logger.debug("Password login button not found, may already be in password login mode")

            phone_input = await self.page.query_selector('input[placeholder*="手机号"], input[placeholder*="邮箱"], input[type="text"]')
            if phone_input:
                logger.debug("Found phone/email input")
                await phone_input.click()
                await asyncio.sleep(0.3)
                await phone_input.type(phone)
                logger.debug(f"Entered phone: {phone}")
                await asyncio.sleep(0.5)

                current_value = await self.page.evaluate("(el) => el.value", phone_input)
                logger.debug(f"Phone input current value: {current_value}")

            password_input = await self.page.query_selector('input[placeholder*="密码"], input[type="password"]')
            if password_input:
                logger.debug("Found password input")
                await password_input.click()
                await asyncio.sleep(0.3)
                await password_input.type(password)
                logger.debug("Entered password")
                await asyncio.sleep(0.5)
            else:
                logger.debug("Password input not found with basic selector, trying all inputs...")
                all_inputs = await self.page.query_selector_all('input')
                logger.debug(f"Found {len(all_inputs)} input elements")
                for i, input_el in enumerate(all_inputs):
                    input_info = await self.page.evaluate(
                        "(el) => ({ placeholder: el.placeholder, type: el.type, className: el.className })",
                        input_el
                    )
                    logger.debug(f"Input {i}: {input_info}")
                    if input_info.get('type') == 'password' or input_info.get('placeholder') and '密码' in input_info.get('placeholder'):
                        logger.debug(f"Found password input at index {i}")
                        await input_el.click()
                        await asyncio.sleep(0.3)
                        await input_el.type(password)
                        logger.debug("Entered password")
                        await asyncio.sleep(0.5)
                        break

            login_button = await self.page.query_selector('button:has-text("登录"), button:has-text("Sign in"), button[type="submit"], [class*="login-btn"], .ds-button--primary')
            if login_button:
                logger.debug("Found login button")
                await login_button.click()
                logger.info("Clicked login button")
                await asyncio.sleep(5)
            else:
                logger.debug("Login button not found with basic selector, trying all buttons...")
                all_buttons = await self.page.query_selector_all('button')
                for i, btn in enumerate(all_buttons):
                    btn_text = await self.page.evaluate("(el) => el.textContent || ''", btn)
                    btn_class = await self.page.evaluate("(el) => el.className || ''", btn)
                    logger.debug(f"Button {i}: text='{btn_text}', class='{btn_class}'")
                    if btn_text.strip() == '登录':
                        await btn.click()
                        logger.info(f"Clicked login button at index {i}")
                        await asyncio.sleep(5)
                        break

            await self.page.wait_for_selector(
                self.SELECTORS["input_box"],
                timeout=120000,
            )
            logger.info("Auto-login successful")

        except Exception as e:
            logger.error(f"Auto-login failed: {e}")
            logger.info("Please complete login manually in the browser window.")

    async def _select_model(self, model_name: str) -> None:
        """
        选择指定的AI模型

        Args:
            model_name: 模型名称
        """
        try:
            model_selector = self.SELECTORS["model_selector"]
            model_element = await self.page.query_selector(model_selector)

            if model_element:
                await model_element.click()
                await asyncio.sleep(1)

                option_selector = f"option:has-text('{model_name}'), [role='option']:has-text('{model_name}')"
                option = await self.page.query_selector(option_selector)

                if option:
                    await option.click()
                    logger.info(f"Selected model: {model_name}")
                else:
                    logger.warning(f"Model {model_name} not found, using default")

        except Exception as e:
            logger.warning(f"Failed to select model: {e}, using default")

    async def _ensure_input_ready(self) -> None:
        """
        确保输入框准备就绪

        等待输入框可见、可编辑，处理可能的弹窗遮挡。
        """
        logger.debug("Ensuring input is ready...")

        try:
            await self.page.wait_for_selector(
                self.SELECTORS["input_box"],
                state="visible",
                timeout=30000,
            )

            input_element = await self.page.query_selector(self.SELECTORS["input_box"])
            if not input_element:
                raise RuntimeError("Input box not found")

            is_editable = await self.page.evaluate(
                "(el) => !el.disabled && !el.readOnly",
                input_element,
            )

            if not is_editable:
                await asyncio.sleep(1)
                is_editable = await self.page.evaluate(
                    "(el) => !el.disabled && !el.readOnly",
                    input_element,
                )

            if not is_editable:
                logger.warning("Input box not editable, trying to click")
                await input_element.click()
                await asyncio.sleep(0.5)

            await self._close_modals_if_present()

        except Exception as e:
            logger.error(f"Failed to ensure input ready: {e}")
            raise

    async def _close_modals_if_present(self) -> None:
        """
        关闭可能存在的弹窗
        """
        try:
            close_selectors = [
                "button[aria-label='Close'], button[aria-label='关闭']",
                ".modal-close, .close-btn",
                "[class*='close']",
            ]

            for selector in close_selectors:
                close_button = await self.page.query_selector(selector)
                if close_button:
                    await close_button.click()
                    await asyncio.sleep(0.5)
                    break

        except Exception as e:
            logger.debug(f"No modals to close or error: {e}")

    async def _type_message(self, message: str) -> None:
        """
        在输入框中输入消息

        使用Playwright的键盘输入方式，模拟真实用户操作：
        1. 点击输入框聚焦
        2. 使用键盘输入文本
        3. 确保输入完成后输入框内容正确

        Args:
            message: 要发送的消息文本
        """
        logger.debug(f"Typing message: {message[:50]}...")

        try:
            input_elements = await self.page.query_selector_all(self.SELECTORS["input_box"])
            logger.debug(f"Found {len(input_elements)} input elements")

            if input_elements:
                input_element = input_elements[0]
                await input_element.click()
                await asyncio.sleep(0.3)

                await self.page.keyboard.type(message, delay=100)
                await asyncio.sleep(0.5)

                current_value = await self.page.evaluate(
                    "(element) => element.value || element.innerText",
                    input_element
                )
                logger.debug(f"Current input value: '{current_value}'")

                if current_value != message:
                    logger.warning(f"Input value mismatch: expected '{message}', got '{current_value}'")
                    await self.page.evaluate(
                        """([element, msg]) => {
                            element.value = msg;
                            element.dispatchEvent(new Event('input', { bubbles: true }));
                        }""",
                        [input_element, message]
                    )
                    await asyncio.sleep(0.3)
                    current_value = await self.page.evaluate(
                        "(element) => element.value",
                        input_element
                    )
                    logger.debug(f"After correction: '{current_value}'")

                logger.debug("Message typed successfully")
            else:
                logger.error("No input box found")
                raise RuntimeError("Input box not found")

        except Exception as e:
            logger.error(f"Failed to type message: {e}")
            raise

    async def _press_send(self) -> None:
        """
        触发发送操作

        使用Playwright原生键盘操作模拟真实用户发送：
        1. 确保输入框正确聚焦
        2. 使用原生键盘Enter键发送
        3. 等待消息发送完成
        4. 验证消息是否真正发送出去
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
                    const textareas = document.querySelectorAll('textarea');
                    for (const ta of textareas) {
                        if (ta.value && ta.value.trim()) {
                            return ta.value;
                        }
                    }
                    return '';
                }
            """)
            logger.debug(f"Input value after send: '{current_value}'")

            if current_value and current_value.strip():
                logger.warning("Input box not cleared, trying send button click")
                await self._try_send_button()

            await asyncio.sleep(2)

            message_count = await self.page.evaluate("""
                () => {
                    const messages = document.querySelectorAll('.ds-message');
                    return messages.length;
                }
            """)
            logger.debug(f"Message count after send: {message_count}")

            user_messages_count = await self.page.evaluate("""
                () => {
                    const messages = document.querySelectorAll('.ds-message');
                    let count = 0;
                    for (const msg of messages) {
                        if (msg.querySelector('.ds-user-avatar') || msg.querySelector('[class*="user"]')) {
                            count++;
                        }
                    }
                    return count;
                }
            """)
            logger.debug(f"User message count: {user_messages_count}")

            if message_count > 0:
                logger.info("Message sent successfully (messages found)")
            elif not current_value or not current_value.strip():
                logger.info("Message sent successfully (input cleared)")
            else:
                logger.warning("Message may not have been sent successfully")

        except Exception as e:
            logger.warning(f"Failed to send: {e}, trying send button")
            await self._try_send_button()

    async def _try_send_button(self) -> None:
        """
        尝试通过发送按钮发送消息
        """
        try:
            send_button = await self.page.query_selector(self.SELECTORS["send_button"])
            if send_button:
                await send_button.click()
                logger.debug("Clicked send button")
            else:
                logger.debug("Send button not found, trying JavaScript click on input")
                await self.page.evaluate("""
                    () => {
                        const textareas = document.querySelectorAll('textarea');
                        for (const ta of textareas) {
                            if (ta.value && ta.value.trim()) {
                                ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                                break;
                            }
                        }
                    }
                """)
        except Exception as e:
            logger.error(f"Failed to send via button: {e}")

    async def _extract_conversation_summary(self) -> str:
        """
        从DeepSeek网页端提取对话总结（标题）

        DeepSeek网页会自动为对话生成总结标题，通常显示在侧边栏或页面顶部。
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
                        '.ds-conversation-title',
                        '.ds-chat-title',
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
                    
                    const h1 = document.querySelector('h1');
                    if (h1) {
                        const text = (h1.textContent || '').trim();
                        if (text && text.length > 0 && text.length < 100) {
                            return text;
                        }
                    }
                    
                    return '';
                }
            """)

            if summary and summary.strip():
                logger.info(f"Extracted conversation summary from DeepSeek: '{summary}'")
                self._conversation_summary = summary.strip()
                return summary.strip()
            else:
                logger.debug("No conversation summary found on DeepSeek page")
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

    async def _wait_for_reply(self) -> str:
        """
        等待AI回复完成并提取内容

        建立可靠的回复完成信号机制：
        1. 等待消息发送后网络活动稳定
        2. 监控文本内容变化
        3. 当文本内容稳定一段时间后判定回复完成

        Returns:
            AI回复的完整文本内容
        """
        logger.debug("Waiting for reply...")

        start_time = time.time()
        self.last_reply_text = ""
        self.stable_count = 0
        valid_reply_found = False

        try:
            await self.page.wait_for_timeout(8000)

            while time.time() - start_time < self.max_wait_time:
                reply_text = await self._extract_reply_content()

                is_valid_reply = False
                if reply_text:
                    if len(reply_text) > 50 and not reply_text.startswith('使用'):
                        is_valid_reply = True
                    elif reply_text.startswith('你好') and len(reply_text) > 20:
                        is_valid_reply = True

                if is_valid_reply:
                    valid_reply_found = True

                if reply_text == self.last_reply_text:
                    self.stable_count += 1
                else:
                    self.stable_count = 0
                    self.last_reply_text = reply_text

                if valid_reply_found and len(self.last_reply_text) > 10 and self.stable_count >= 5:
                    logger.debug(f"Reply stable for 5 checks, assuming complete")
                    break

                if await self._is_reply_complete():
                    logger.debug("Reply completion detected")
                    break

                await asyncio.sleep(1)

            await asyncio.sleep(8)
            self.last_reply_text = await self._extract_reply_content()
            logger.debug(f"Final extracted content: {self.last_reply_text[:100]}...")

            if self.stable_count < 5 and not await self._is_reply_complete():
                logger.warning("Reply wait timeout, returning current content")

            return self.last_reply_text

        except PlaywrightTimeoutError:
            logger.error("Timeout waiting for assistant message")
            raise
        except Exception as e:
            logger.error(f"Error waiting for reply: {e}")
            raise

    async def _stream_reply(self) -> AsyncGenerator[str, None]:
        """
        以流式方式获取AI回复

        Yields:
            回复文本片段
        """
        logger.debug("Streaming reply...")

        start_time = time.time()
        last_text = ""
        stable_count = 0
        valid_reply_found = False

        try:
            await self.page.wait_for_timeout(5000)

            while time.time() - start_time < self.max_wait_time:
                current_text = await self._extract_reply_content()

                if current_text != last_text:
                    delta = current_text[len(last_text) :]
                    if delta:
                        yield delta
                    last_text = current_text
                    stable_count = 0
                else:
                    stable_count += 1

                if current_text:
                    if len(current_text) > 50 and not current_text.startswith('使用'):
                        valid_reply_found = True
                    elif current_text.startswith('你好') and len(current_text) > 20:
                        valid_reply_found = True

                if valid_reply_found and len(last_text) > 10 and stable_count >= 8:
                    logger.debug(f"Reply stable for 8 checks, assuming complete")
                    break

                if await self._is_reply_complete():
                    remaining = current_text[len(last_text) :]
                    if remaining:
                        yield remaining
                    logger.debug("Reply completion detected")
                    break

                await asyncio.sleep(0.5)

            logger.debug(f"Streaming complete, total content: {len(last_text)} chars")

        except Exception as e:
            logger.error(f"Error streaming reply: {e}")
            raise

    async def _extract_reply_content(self) -> str:
        """
        提取AI回复的正文内容

        通过JavaScript精确定位DOM元素，提取HTML内容，
        然后转换为Markdown格式，保留表格、列表、代码块等富文本结构。

        Returns:
            提取的回复文本内容（Markdown格式）
        """
        try:
            js_code = """
                () => {
                    const debugInfo = [];
                    
                    const sidebar = document.querySelector('._6d215eb, [class*="sidebar"], [class*="history"], nav');
                    
                    const allMessages = document.querySelectorAll('.ds-message');
                    debugInfo.push('Total ds-message elements: ' + allMessages.length);
                    
                    const allCodeBlocks = [];
                    
                    for (let i = allMessages.length - 1; i >= 0; i--) {
                        const msg = allMessages[i];
                        
                        if (sidebar && sidebar.contains(msg)) {
                            continue;
                        }
                        
                        const hasAssistantAvatar = msg.querySelector('.ds-assistant-avatar, .ds-avatar--assistant');
                        const hasUserAvatar = msg.querySelector('.ds-user-avatar, .ds-avatar--user');
                        const hasAssistantContent = msg.querySelector('.ds-assistant-message-main-content');
                        const hasMarkdown = msg.querySelector('.ds-markdown');
                        const isUser = msg.querySelector('[class*="user"]') !== null;
                        
                        const isAssistant = (hasAssistantAvatar && !hasUserAvatar) || 
                                           hasAssistantContent || 
                                           (hasMarkdown && !isUser);
                        
                        if (isAssistant) {
                            const content = msg.querySelector('.ds-assistant-message-main-content, .ds-markdown');
                            if (content) {
                                const text = content.textContent.trim();
                                if (text.length > 10) {
                                    const containsModeSelect = text.includes('快速模式') && text.includes('专家模式');
                                    const containsWelcome = text.includes('使用快速模式开始对话');
                                    const containsHistory = text.includes('今天') && text.includes('昨天') && text.includes('初次问候');
                                    
                                    if (!containsModeSelect && !containsWelcome && !containsHistory) {
                                        const clone = content.cloneNode(true);
                                        
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
                                        
                                        debugInfo.push('Found valid assistant message: ' + text.length + ' chars');
                                        return { found: true, html: clone.innerHTML, text: text.replace(/[\\n\\r]+/g, '\\n'), codeBlocks: allCodeBlocks, debug: debugInfo.join(' | ') };
                                    } else {
                                        debugInfo.push('Skipping invalid content (mode select/welcome/history)');
                                    }
                                }
                            }
                        }
                    }
                    
                    const assistantContent = document.querySelector('.ds-assistant-message-main-content');
                    if (assistantContent) {
                        if (sidebar && sidebar.contains(assistantContent)) {
                            debugInfo.push('Assistant content in sidebar, skipping');
                        } else {
                            const text = assistantContent.textContent.trim();
                            if (text.length > 10) {
                                const clone = assistantContent.cloneNode(true);
                                let codeBlockIndex = 0;
                                clone.querySelectorAll('pre').forEach(pre => {
                                    if (pre.querySelector('table')) {
                                        return;
                                    }
                                    
                                    const preClone = pre.cloneNode(true);
                                    preClone.querySelectorAll('button, [role="button"], .copy-btn, .run-btn, [class*="toolbar"], [class*="header"]').forEach(el => el.remove());
                                    
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
                                debugInfo.push('Found ds-assistant-message-main-content: ' + text.length + ' chars');
                                return { found: true, html: clone.innerHTML, text: text.replace(/[\\n\\r]+/g, '\\n'), codeBlocks: allCodeBlocks, debug: debugInfo.join(' | ') };
                            }
                        }
                    }
                    
                    const markdownContent = document.querySelector('.ds-markdown');
                    if (markdownContent) {
                        if (sidebar && sidebar.contains(markdownContent)) {
                            debugInfo.push('Markdown content in sidebar, skipping');
                        } else {
                            const text = markdownContent.textContent.trim();
                            if (text.length > 10) {
                                const clone = markdownContent.cloneNode(true);
                                let codeBlockIndex = 0;
                                clone.querySelectorAll('pre').forEach(pre => {
                                    if (pre.querySelector('table')) {
                                        return;
                                    }
                                    
                                    const preClone = pre.cloneNode(true);
                                    preClone.querySelectorAll('button, [role="button"], .copy-btn, .run-btn, [class*="toolbar"], [class*="header"]').forEach(el => el.remove());
                                    
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
                                debugInfo.push('Found ds-markdown: ' + text.length + ' chars');
                                return { found: true, html: clone.innerHTML, text: text.replace(/[\\n\\r]+/g, '\\n'), codeBlocks: allCodeBlocks, debug: debugInfo.join(' | ') };
                            }
                        }
                    }
                    
                    const chatArea = document.querySelector('._3586175') || document.querySelector('.ds-virtual-list');
                    if (chatArea) {
                        const contentInChatArea = chatArea.querySelector('[class*="content"], .ds-markdown');
                        if (contentInChatArea) {
                            const text = contentInChatArea.textContent.trim();
                            if (text.length > 10) {
                                const clone = contentInChatArea.cloneNode(true);
                                let codeBlockIndex = 0;
                                clone.querySelectorAll('pre').forEach(pre => {
                                    if (pre.querySelector('table')) {
                                        return;
                                    }
                                    
                                    const preClone = pre.cloneNode(true);
                                    preClone.querySelectorAll('button, [role="button"], .copy-btn, .run-btn, [class*="toolbar"], [class*="header"]').forEach(el => el.remove());
                                    
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
                                debugInfo.push('Found content in chat area: ' + text.length + ' chars');
                                return { found: true, html: clone.innerHTML, text: text.replace(/[\\n\\r]+/g, '\\n'), codeBlocks: allCodeBlocks, debug: debugInfo.join(' | ') };
                            }
                        }
                    }
                    
                    return { found: false, html: '', text: '', codeBlocks: [], debug: debugInfo.join(' | ') || 'No valid content found' };
                }
            """
            
            result = await self.page.evaluate(js_code)
            
            if result.get('found', False):
                logger.debug(f"Content extraction debug: {result.get('debug', '')}")
                html = result.get('html', '')
                text = result.get('text', '')
                code_blocks = result.get('codeBlocks', [])
                
                if html:
                    try:
                        from markdownify import markdownify as html_to_markdown
                        markdown = html_to_markdown(html, heading_style="ATX")
                        logger.debug(f"HTML converted to Markdown, length: {len(markdown)}")
                        logger.debug(f"Markdown preview: {markdown[:200]}...")
                        
                        for i, code_block in enumerate(code_blocks):
                            markdown = markdown.replace(f'CODEBLOCKPLACEHOLDER{i}END', code_block)
                        
                        import re
                        markdown = re.sub(r'\[-?\d+\]\([^)]+\)', '', markdown)
                        markdown = re.sub(r'\[-?\d+\]', '', markdown)
                        lines = markdown.split('\n')
                        cleaned_lines = []
                        for line in lines:
                            stripped = line.strip()
                            if stripped not in ['复制', '下载', 'text']:
                                cleaned_lines.append(line)
                        markdown = '\n'.join(cleaned_lines)
                        markdown = re.sub(r'\n{3,}', '\n\n', markdown)
                        markdown = markdown.strip()
                        
                        logger.debug(f"Markdown after cleaning: {markdown[:200]}...")
                        return markdown
                    except Exception as e:
                        logger.warning(f"Failed to convert HTML to Markdown: {e}, using plain text fallback")
                
                logger.debug(f"Extracted content: {text[:200]}...")
                return text
            else:
                logger.debug(f"Content extraction debug: {result.get('debug', '')}")
                return ""

        except Exception as e:
            logger.error(f"Failed to extract reply content: {e}")
            return ""

    async def _is_reply_complete(self) -> bool:
        """
        判断AI回复是否完成

        通过多种信号判断回复状态：
        1. 检查loading指示器是否消失
        2. 检查停止按钮是否消失
        3. 检查是否有新的消息气泡出现
        4. 检查消息节点的状态属性

        Returns:
            True表示回复完成，False表示仍在进行中
        """
        try:
            loading_elements = await self.page.query_selector_all(
                self.SELECTORS["loading_indicator"]
            )
            if loading_elements:
                return False

            stop_button = await self.page.query_selector(self.SELECTORS["stop_button"])
            if stop_button:
                is_hidden = await self.page.evaluate(
                    "(el) => el.offsetParent === null",
                    stop_button,
                )
                if not is_hidden:
                    return False

            messages = await self.page.query_selector_all(self.SELECTORS["assistant_message"])
            if messages:
                last_message = messages[-1]
                is_complete = await self.page.evaluate(
                    """(el) => {
                        if (el.dataset.completed === 'true') return true;
                        if (el.classList.contains('completed')) return true;
                        if (el.getAttribute('aria-busy') === 'false') return true;
                        return false;
                    }""",
                    last_message,
                )
                if is_complete:
                    return True

            return False

        except Exception as e:
            logger.debug(f"Error checking reply completion: {e}")
            return False

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
        1. 侧边栏当前对话标题
        2. 最后一条AI回复
        3. 页面标题
        4. JavaScript遍历DOM
        """
        try:
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

            for selector in selectors:
                try:
                    locator = self.page.locator(selector).first
                    await locator.wait_for(timeout=3000)
                    text = await locator.text_content()
                    if text and text.strip():
                        summary = text.strip()
                        logger.info(f"ChatGPT summary via selector '{selector}': '{summary}'")
                        return summary
                except Exception:
                    continue

            logger.info("ChatGPT sidebar selectors failed, trying AI reply extraction...")
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

            logger.info("ChatGPT AI reply extraction failed, trying page title...")
            try:
                title = await self.page.title()
                if title and title.strip():
                    summary = title.strip()
                    logger.info(f"ChatGPT summary via page title: '{summary}'")
                    return summary
            except Exception:
                pass

            logger.info("ChatGPT summary extraction failed, trying JavaScript evaluation...")
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
                        'h1',
                        'h2',
                    ];
                    
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (el) {
                            const text = el.textContent.trim();
                            if (text && text.length > 0 && text.length < 200) {
                                return text;
                            }
                        }
                    }
                    
                    const assistantMessages = document.querySelectorAll('[data-message-author="assistant"], [class*="assistant"], [role="article"]');
                    if (assistantMessages.length > 0) {
                        const lastMsg = assistantMessages[assistantMessages.length - 1];
                        return lastMsg.textContent.trim().substring(0, 200);
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
