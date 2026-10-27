import os
from deepeval.models import LocalModel, TypeSafeModel
from app.core.config import get_settings

settings = get_settings()

class JudgeModels:
    _instance = None

    def __new__(cls):
        # Singleton
        if cls._instance is None:
            cls._instance = super(JudgeModels, cls).__new__(cls)
            cls._instance._initialize_models()
        return cls._instance

    def _initialize_models(self):
        self.llm = LocalModel(
            model=settings.llm_model, 
            base_url=settings.llm_base_url,  
            api_key=settings.llm_api_key.get_secret_value(),          
        )

        if hasattr(settings, "jev_url") and settings.jev_base_url:
            os.environ["TYPESAFE_BASE_URL"] = settings.jev_base_url

        self.jev = TypeSafeModel(
            model=settings.jev_model, 
            api_key=settings.jev_api_key.get_secret_value(),          
        )

# Global Instance
judge = JudgeModels()