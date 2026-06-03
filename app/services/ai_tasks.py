import uuid
from sqlalchemy.orm import Session
from sqlalchemy import select
from langchain_openai import OpenAIEmbeddings
from app.models import Recipe
from app.constants import EMBEDDING_MODEL
from app.utils.recipe_search_text import content_text_from_recipe


def process_single_recipe_embedding_sync(recipe_id: uuid.UUID, db: Session):
    """
    Generates and saves the vector embedding for a single recipe synchronously.
    (Used temporarily for MVP E2E testing).
    """

    embeddings_client = OpenAIEmbeddings(model=EMBEDDING_MODEL)

    # fetch recipe data
    recipe = db.scalars(select(Recipe).filter(Recipe.id == recipe_id)).first()

    if not recipe:
        print(f"Vectorization skipped: Recipe {recipe_id} not found.")
        return

    content_text = content_text_from_recipe(recipe)
    if not content_text:
        print(f"Vectorization skipped: Recipe {recipe_id} content missing.")
        return

    recipe.content_text = content_text

    vector_list = embeddings_client.embed_documents([content_text])
    vector = vector_list[0]

    # update db record with new vector
    recipe.embedding = vector
    db.add(recipe)
    db.commit()
    print(f"✅ SYNCHRONOUS VECTORIZATION COMPLETE for {recipe.name}")
