import unittest

from json_tools import (
    JsonToolError, parse_json_like, path_at_position, render_json, transform,
    searchable_spans, value_stats,
)


class JsonToolsTest(unittest.TestCase):
    def test_extracts_prefix_and_suffix(self):
        output, prefix, suffix, value = transform('INFO: x {"a":1,"中文":true} trailing')
        self.assertEqual(value, {"a": 1, "中文": True})
        self.assertEqual(prefix, 8)
        self.assertEqual(suffix, 9)
        self.assertIn('"中文": true', output)

    def test_compact(self):
        output, *_ = transform(' [1, {"x": null}] ', compact=True)
        self.assertEqual(output, '[1,{"x":null}]')

    def test_key_styles_only_change_keys(self):
        source = '{"normal":"a:b", "has space": "value", "quote\\\"key": 2}'
        single, *_ = transform(source, key_style="single")
        self.assertIn("'normal': \"a:b\"", single)
        bare, *_ = transform(source, key_style="bare")
        self.assertIn('normal: "a:b"', bare)
        self.assertIn('"has space": "value"', bare)

    def test_repeated_style_changes_keep_outer_object(self):
        value = {"skills": ["Java", "SQL", "Docker"], "name": "developer"}
        bare = render_json(value, key_style="bare")
        self.assertTrue(bare.startswith("{"))
        single = render_json(value, key_style="single")
        self.assertIn("'skills'", single)
        restored = render_json(value, compact=True, key_style="double")
        self.assertEqual(restored, '{"skills":["Java","SQL","Docker"],"name":"developer"}')

    def test_invalid_outer_object_does_not_fall_back_to_inner_array(self):
        with self.assertRaises(JsonToolError):
            transform('{skills: ["Java", "SQL", "Docker"]}')

    def test_parses_and_detects_mixed_mode(self):
        source = "{'name':\"张三\",age:30,\"active\":true,\"skills\":[\"Java\",\"SQL\",\"Docker\"],\"address\":{\"city\":\"广州\",\"postalCode\":\"510000\"},\"phone\":null}"
        parsed = parse_json_like(source)
        self.assertTrue(parsed.mixed)
        self.assertEqual(parsed.key_styles, frozenset({"single", "double", "bare"}))
        self.assertEqual(parsed.value["name"], "张三")
        self.assertEqual(parsed.value["address"]["city"], "广州")

    def test_pure_single_key_mode_is_not_mixed(self):
        parsed = parse_json_like("{'name': \"张三\", 'age': 30}")
        self.assertFalse(parsed.mixed)
        self.assertEqual(parsed.key_styles, frozenset({"single"}))

    def test_searchable_key_and_value_spans(self):
        text = '{"name":"Alice", age:30, \'city\':"广州"}'
        keys, values = searchable_spans(text)
        self.assertEqual([text[start:end] for start, end in keys], ['"name"', 'age', "'city'"])
        self.assertEqual([text[start:end] for start, end in values], ['"Alice"', '30', '"广州"'])

    def test_paths(self):
        text, *_ = transform('{"user":{"items":[{"name":"Alice"}]}}')
        pos = text.index("Alice")
        self.assertEqual(path_at_position(text, pos), "$.user.items[0].name")
        self.assertEqual(path_at_position(text, pos, False), "user.items[0].name")

    def test_special_key_path(self):
        text, *_ = transform('{"a b": 1}')
        self.assertEqual(path_at_position(text, text.index("1")), "$['a b']")

    def test_stats(self):
        self.assertEqual(value_stats({"a": [{"b": 1}]}), (3, 3))

    def test_invalid(self):
        with self.assertRaises(JsonToolError):
            transform("not json")


if __name__ == "__main__":
    unittest.main()
