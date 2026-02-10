from pydantic import BaseModel


# 用户上下文模型
class UserContext(BaseModel):
    user_id: str
