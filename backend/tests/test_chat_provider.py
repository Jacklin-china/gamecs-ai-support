from app.config import Settings


def test_chat_provider_selects_the_correct_key_endpoint_and_model():
    shared = {"deepseek_api_key":"deep-key", "deepseek_base_url":"https://deep.example", "deepseek_model":"deep-model",
              "siliconflow_api_key":"silicon-key", "siliconflow_base_url":"https://silicon.example", "siliconflow_chat_model":"silicon-model"}
    deep = Settings(_env_file=None, chat_provider="deepseek", **shared)
    silicon = Settings(_env_file=None, chat_provider="siliconflow", **shared)
    assert (deep.chat_api_key, deep.chat_base_url, deep.chat_model) == ("deep-key", "https://deep.example", "deep-model")
    assert (silicon.chat_api_key, silicon.chat_base_url, silicon.chat_model) == ("silicon-key", "https://silicon.example", "silicon-model")
