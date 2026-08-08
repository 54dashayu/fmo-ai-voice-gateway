import os
import unittest
from unittest.mock import patch

import gateway


class GatewaySafetyTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                gateway.build_request("BH1JSS", "测试")

    def test_rejects_unlisted_callsign(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOWED_CALLSIGNS": "BH1JSS", "DASHSCOPE_API_KEY": "not-a-real-key"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(PermissionError):
                gateway.build_request("OTHER", "测试")

    def test_all_callsigns_mode_still_honors_blacklist(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOW_ALL_CALLSIGNS": "true", "DASHSCOPE_API_KEY": "not-a-real-key"}
        with patch.dict(os.environ, env, clear=True), patch.object(gateway.callsign_policy, "load_blacklist", return_value=set()):
            gateway.build_request("BG1ABC", "测试")
        with patch.dict(os.environ, env, clear=True), patch.object(gateway.callsign_policy, "load_blacklist", return_value={"BG1ABC"}):
            with self.assertRaises(PermissionError):
                gateway.build_request("BG1ABC", "测试")

    def test_builds_beijing_request_without_logging_key(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOWED_CALLSIGNS": "BH1JSS", "DASHSCOPE_API_KEY": "not-a-real-key"}
        with patch.dict(os.environ, env, clear=True):
            url, payload, headers = gateway.build_request("bh1jss", " 你好 ")
        self.assertEqual(url, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(payload["messages"][1]["content"], "你好")
        self.assertIn("Authorization", headers)

    def test_routes_knowledge_and_normal_chat(self):
        self.assertTrue(gateway.is_knowledge_question("FMO仪表盘怎么连接"))
        self.assertTrue(gateway.is_knowledge_question("天线驻波是什么"))
        self.assertFalse(gateway.is_knowledge_question("你好，今天心情怎么样"))

    def test_detects_standard_amateur_radio_qso(self):
        self.assertTrue(gateway.is_standard_qso("CQ CQ CQ，这里是BG1ABC，请回答"))
        self.assertTrue(gateway.is_standard_qso("Bravo Golf One Alpha Bravo Charlie，73"))
        self.assertTrue(gateway.is_standard_qso("BH1JSS请过来"))
        self.assertFalse(gateway.is_standard_qso("你好，今天过得怎么样"))

    def test_explicit_spoken_modes(self):
        self.assertEqual(gateway.route_mode("普通聊天，今天怎么样"), ("chat", "今天怎么样"))
        self.assertEqual(gateway.route_mode("知识问答：仪表盘怎么连接"), ("knowledge", "仪表盘怎么连接"))

    def test_chat_and_knowledge_have_distinct_instructions(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOWED_CALLSIGNS": "BH1JSS", "DASHSCOPE_API_KEY": "not-a-real-key"}
        with patch.dict(os.environ, env, clear=True):
            _, chat, _ = gateway.build_request("BH1JSS", "聊聊天", [], "chat")
            _, knowledge, _ = gateway.build_request(
                "BH1JSS", "怎么连接", [{"title": "说明", "content": "连接方法"}], "knowledge"
            )
        self.assertIn("自然、友好、轻松", chat["messages"][0]["content"])
        self.assertIn("优先依据", knowledge["messages"][0]["content"])

    def test_qso_prompt_uses_source_callsign_and_forbids_fake_rst(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOWED_CALLSIGNS": "BG1ABC", "DASHSCOPE_API_KEY": "test"}
        with patch.dict(os.environ, env, clear=True):
            _, payload, _ = gateway.build_request("BG1ABC", "CQ CQ，这里是BG1ABC，请回答", [], "chat")
        prompt = payload["messages"][0]["content"]
        self.assertIn("对方呼号为BG1ABC", prompt)
        self.assertIn("ITU字母解释法", prompt)
        self.assertIn("不得虚构RST", prompt)

    def test_rag_context_contains_source(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOWED_CALLSIGNS": "BH1JSS", "DASHSCOPE_API_KEY": "not-a-real-key"}
        knowledge = [{"title": "仪表盘说明", "heading": "连接", "content": "使用服务器地址连接。"}]
        with patch.dict(os.environ, env, clear=True):
            _, payload, _ = gateway.build_request("BH1JSS", "怎么连接", knowledge)
        self.assertIn("仪表盘说明", payload["messages"][0]["content"])

    def test_knowledge_search_filters_unrelated_vector_matches(self):
        env = {
            "DASHSCOPE_API_KEY": "test", "FMO_KB_ADMIN_TOKEN": "test",
            "FMO_KB_MIN_SCORE": "0.55", "FMO_KB_ENABLED": "true",
        }
        response = {"results": [
            {"score": 0.81, "title": "相关资料"},
            {"score": 0.43, "title": "无关资料"},
        ]}
        with patch.dict(os.environ, env, clear=True), patch.object(gateway, "embed_text", return_value=[0.1]), patch.object(
            gateway, "_post_json", return_value=response
        ):
            results = gateway.search_knowledge("测试问题")
        self.assertEqual([item["title"] for item in results], ["相关资料"])

    def test_empty_knowledge_falls_back_to_bailian_web_search(self):
        env = {
            "FMO_AI_ENABLED": "true",
            "FMO_ALLOWED_CALLSIGNS": "BH1JSS",
            "DASHSCOPE_API_KEY": "not-a-real-key",
            "FMO_KB_ADMIN_TOKEN": "not-a-real-token",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(gateway, "search_knowledge", return_value=[]), patch.object(
            gateway, "call_model", return_value="根据联网查询，驻波比用于衡量传输线与天线的匹配情况。"
        ) as call_model:
            result = gateway.answer("BH1JSS", "FMO仪表盘怎么使用")
        self.assertEqual(result["fallback"], "bailian_web_search")
        self.assertEqual(result["sources"][0]["title"], "阿里百炼联网搜索")
        self.assertTrue(call_model.call_args.kwargs["web_search"])

    def test_disabled_knowledge_does_not_require_nas(self):
        env = {
            "FMO_AI_ENABLED": "true",
            "FMO_ALLOWED_CALLSIGNS": "BH1JSS",
            "DASHSCOPE_API_KEY": "not-a-real-key",
            "FMO_KB_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            gateway, "search_knowledge", side_effect=AssertionError("NAS must not be called")
        ), patch.object(gateway, "call_model", return_value="联网回答") as call_model:
            result = gateway.answer("BH1JSS", "天线驻波是什么")
        self.assertEqual(result["fallback"], "bailian_web_search")
        self.assertTrue(call_model.call_args.kwargs["web_search"])

    def test_web_search_payload_is_only_enabled_for_fallback(self):
        env = {"FMO_AI_ENABLED": "true", "FMO_ALLOWED_CALLSIGNS": "BH1JSS", "DASHSCOPE_API_KEY": "test"}
        with patch.dict(os.environ, env, clear=True):
            _, normal, _ = gateway.build_request("BH1JSS", "驻波是什么", [], "knowledge")
            _, fallback, _ = gateway.build_request("BH1JSS", "驻波是什么", [], "knowledge", web_search=True)
        self.assertNotIn("enable_search", normal)
        self.assertTrue(fallback["enable_search"])
        self.assertTrue(fallback["search_options"]["forced_search"])


if __name__ == "__main__":
    unittest.main()
