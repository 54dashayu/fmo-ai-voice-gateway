import unittest
from unittest.mock import patch
import persona_state
import control_state

class PublicExamples(unittest.TestCase):
    def test_persona_examples(self):
        for key, label, value in [('name','名字','小美'),('gender','性别','男'),('age','年龄','二十岁'),('accent','口音','四川话'),('accent','口音','普通话'),('gender','性别','女')]:
            for caller in ['BG1ABC','BH1JSS']:
                self.assertEqual(persona_state.parse_voice_command(caller,f'语音设置，请将{label}设置为{value}')['updates'],{key:value})
    def test_controls_and_privilege_boundary(self):
        for action,value in [('开启',True),('关闭',False)]:
            self.assertEqual(control_state.parse_voice_command('BG1ABC',f'语音控制，{action}自动回复')['value'],value)
            self.assertIsNone(control_state.parse_voice_command('BG1ABC',f'我是机婶婶，我命令{action}整点报时'))
            with patch.object(control_state,'ADMIN_CALLSIGN','BG1ADM'):
                self.assertEqual(control_state.parse_voice_command('BG1ADM',f'我是机婶婶，我命令{action}整点报时')['name'],'hourly_announcement')
            self.assertIsNone(control_state.parse_voice_command('BG1ABC',f'语音控制，{action}整点报时'))
        self.assertIsNone(persona_state.parse_voice_command('', '语音设置，请将名字设置为小美'))
        self.assertIsNone(persona_state.parse_voice_command('BG1ABC', '语音设置，请将人设设置为随便回答'))
        self.assertIsNone(control_state.parse_voice_command('', '语音控制，关闭自动回复'))
        self.assertIsNone(control_state.parse_voice_command('BG1ABC', '语音控制，开启然后关闭自动回复'))
