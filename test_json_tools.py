import unittest

from json_tools import (
    JsonToolError, format_json_like, json5_minify_risks, parse_json_like, path_at_position, render_json,
    rewrite_json_like_quotes, transform,
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

    def test_readable_output_uses_stable_tab_indentation(self):
        output = render_json({"ascii": 1, "中文字段": 2, "": 3})
        property_lines = output.splitlines()[1:4]
        self.assertTrue(all(line.startswith("\t") for line in property_lines))
        self.assertTrue(all(not line.startswith(" ") for line in property_lines))

    def test_key_styles_only_change_keys(self):
        source = '{"normal":"a:b", "has space": "value", "quote\\\"key": 2}'
        single, *_ = transform(source, key_style="single")
        self.assertIn("'normal': \"a:b\"", single)
        bare, *_ = transform(source, key_style="bare")
        self.assertIn('normal: "a:b"', bare)
        self.assertIn('"has space": "value"', bare)

    def test_quote_rewrites_preserve_layout_comments_and_unrelated_tokens(self):
        source = "{ name: 'Alice', /* note */ \"city\" : 'Taipei', 'tags': [\"JSON\", .5,], }"

        double, _, escaped = rewrite_json_like_quotes(source, value_quote="double")
        self.assertEqual(escaped, 0)
        self.assertIn('name: "Alice"', double)
        self.assertIn('/* note */ "city" : "Taipei"', double)
        self.assertIn("'tags': [\"JSON\", .5,], }", double)

        key_single, _, escaped = rewrite_json_like_quotes(source, key_style="single")
        self.assertEqual(escaped, 0)
        self.assertIn("'name': 'Alice'", key_single)
        self.assertIn("/* note */ 'city' : 'Taipei'", key_single)
        self.assertIn("'tags': [\"JSON\", .5,], }", key_single)

    def test_value_quote_rewrite_reports_when_target_quote_needs_escaping(self):
        source = """{note: "He said 'hi'", other: "plain"}"""
        output, _, escaped = rewrite_json_like_quotes(source, value_quote="single")
        self.assertEqual(escaped, 1)
        self.assertIn("note: 'He said \\'hi\\''", output)
        self.assertIn("other: 'plain'", output)

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

    def test_json5_parser_supports_comments_relaxed_numbers_and_trailing_commas(self):
        source = '''{
            // User profile
            name: 'Alice',
            ratio: .5,
            color: 0xFFAA00,
            offset: +1,
            tags: ['json', 'json5',],
        }'''

        parsed = parse_json_like(source)

        self.assertEqual(parsed.value["name"], "Alice")
        self.assertEqual(parsed.value["ratio"], 0.5)
        self.assertEqual(parsed.value["color"], 0xFFAA00)
        self.assertEqual(parsed.value["offset"], 1)
        self.assertEqual(parsed.value["tags"], ["json", "json5"])

    def test_json5_parser_supports_unicode_and_escaped_unquoted_keys(self):
        source = r'''{
            中文键名: '可以使用',
            $enabled: true,
            \u0066oo: .5,
        }'''

        output, parsed = format_json_like(source)

        self.assertEqual(parsed.value, {"中文键名": "可以使用", "$enabled": True, "foo": 0.5})
        self.assertIn("中文键名: '可以使用'", output)
        self.assertIn(r"\u0066oo: .5", output)

    def test_json5_minify_risks_ignore_standard_json_whitespace(self):
        source = '''{
            "name": "Alice",
            "items": [1, 2]
        }'''
        self.assertEqual(json5_minify_risks(source), frozenset())

    def test_json5_minify_risks_detects_lossy_syntax(self):
        source = '''{
            // note
            name: 'Alice',
            ratio: .5,
            tags: [1,],
            continued: "first\\
second",
        }'''
        self.assertEqual(
            json5_minify_risks(source),
            frozenset({
                "comments", "bare_keys", "single_quotes", "json5_literals",
                "trailing_commas", "json5_string_escapes",
            }),
        )

    def test_lossless_json5_formatter_keeps_comments_literals_and_trailing_commas(self):
        source = '''{
// User profile
name:'Alice',
ratio:.5,
color:0xFFAA00, /* theme color */
tags:['json','json5',],
message:'first\\
second',
}'''

        output, parsed = format_json_like(source)

        self.assertEqual(parsed.value["color"], 0xFFAA00)
        self.assertIn("// User profile", output)
        self.assertIn("name: 'Alice'", output)
        self.assertIn("ratio: .5", output)
        self.assertIn("color: 0xFFAA00, /* theme color */", output)
        self.assertIn("'json5',\n\t]", output)
        self.assertIn("'first\\\nsecond'", output)

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
