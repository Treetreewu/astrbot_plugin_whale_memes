"""MaiBot SDK entry point, using the same bundled learning core as AstrBot."""
import json

from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

try:
    from .whale_memes.backends import MaiBackend
    from .whale_memes.config import Config
    from .whale_memes.learning import DESCRIPTION, Learning
except ImportError:  # MaiBot's standalone module loader / command-line checks.
    from whale_memes.backends import MaiBackend
    from whale_memes.config import Config
    from whale_memes.learning import DESCRIPTION, Learning


class PluginSettings(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class LearningSettings(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用查梗学习")
    ttl_days: int = Field(default=30, description="资料复核周期（天）")
    max_notes_per_scope: int = Field(default=500, description="每会话最大笔记数")
    max_notes_total: int = Field(default=5000, description="数据库最大笔记数")
    max_searches: int = Field(default=2, description="每次最多查询数（1–2）")
    max_results: int = Field(default=3, description="每次查询最多候选数（1–5）")
    max_pages: int = Field(default=2, description="每次最多读取页面数（0–3）")
    max_concurrent: int = Field(default=2, description="最大并发学习数（1–4）")
    learning_per_hour: int = Field(default=6, description="每会话每小时最多学习次数，失败也计入")
    timeout_seconds: int = Field(default=45, description="单次学习总超时秒数（5–50）")
    operation_timeout_seconds: int = Field(default=12, description="单次外部调用超时秒数（1–30）")
    max_source_chars: int = Field(default=8000, description="每个来源最多输入字符")
    allow_model_discovery: bool = Field(default=True, description="原生搜索不可用时允许模型发现链接；不能直接作为证据")
    allow_search_excerpt: bool = Field(default=True, description="允许基于原生搜索摘要学习，始终标注摘要等级")
    storage_path: str = Field(default="", description="数据库路径，留空使用原生数据目录")
    model_task: str = Field(default="learner", description="原生模型任务名")
    provider_id: str = Field(default="", description="仅 AstrBot 使用的模型覆盖，此处留空")
    maibot_persona: str = Field(default="maibot-native-v1", description="MaiBot 笔记人格作用域")


class PluginConfig(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    learning: LearningSettings = Field(default_factory=LearningSettings)


class MemePlugin(MaiBotPlugin):
    config_model = PluginConfig

    async def on_load(self):
        await self.reconfigure()

    async def reconfigure(self):
        settings = Config.load(self.config.learning.model_dump())
        old = getattr(self, "learning", None)
        if old:
            await old.close()
        self.settings = settings
        path = settings.storage_path or str(self.ctx.data_dir / "notes.sqlite3")
        self.learning = Learning(path, settings)

    async def on_unload(self):
        if getattr(self, "learning", None):
            await self.learning.close()

    async def on_config_update(self, scope, config_data, version):
        await self.reconfigure()

    @Tool("meme_notebook", description=DESCRIPTION, parameters=[
        ToolParameterInfo(name="action", param_type=ToolParamType.STRING,
            description="learn / refresh / lookup / forget", required=True),
        ToolParameterInfo(name="term", param_type=ToolParamType.STRING,
            description="公共梗名，80字内；forget 使用完整条目名", required=True),
    ])
    async def meme_notebook(self, action: str, term: str, stream_id: str = "", **kwargs):
        if not getattr(self, "learning", None):
            result = {"ok": False, "status": "not_initialized"}
        else:
            result = await self.learning.run("maibot", self.settings.maibot_persona, stream_id,
                MaiBackend(self.ctx, self.settings), action, term)
        return {"name": "meme_notebook", "content": json.dumps(result, ensure_ascii=False)}


def create_plugin():
    return MemePlugin()
