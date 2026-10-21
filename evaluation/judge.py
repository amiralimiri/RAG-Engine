import os
from deepeval.models import LocalModel, TypeSafeModel
from src import config

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
            model=config.MODEL, 
            base_url=config.BASE_URL,  
            api_key=config.API_KEY.get_secret_value(),          
        )

        if hasattr(config, "JEV_URL") and config.JEV_URL:
            os.environ["TYPESAFE_BASE_URL"] = config.JEV_URL

        self.jev = TypeSafeModel(
            model=config.JEV_MODEL, 
            api_key=config.API_KEY.get_secret_value(),          
        )

# Global Instance
judge = JudgeModels()