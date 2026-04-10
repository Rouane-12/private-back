import os
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)


def upload_photo(file_bytes: bytes, folder: str = "maison_or/photos", public_id: str = None) -> dict:
    kwargs = {
        "folder": folder,
        "resource_type": "image",
        "transformation": [{"quality": "auto", "fetch_format": "auto"}],
    }
    if public_id:
        kwargs["public_id"] = public_id

    result = cloudinary.uploader.upload(file_bytes, **kwargs)

    thumbnail_url = cloudinary.CloudinaryImage(result["public_id"]).build_url(
        width=400, height=400, crop="fill", quality="auto"
    )

    return {
        "url": result["secure_url"],
        "public_id": result["public_id"],
        "thumbnail_url": thumbnail_url,
    }


def delete_photo(public_id: str):
    cloudinary.uploader.destroy(public_id)