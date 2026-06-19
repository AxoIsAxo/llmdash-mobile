from sqlalchemy import Column, Integer, String, Text, Boolean
from ..database import Base


class SkillConfigDB(Base):
    __tablename__ = "skill_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=False)
    input_schema_json = Column(Text, nullable=False)
    source = Column(String(32), nullable=False, default="db")
    enabled = Column(Boolean, nullable=False, default=True)
    user_id = Column(Integer, nullable=True)
