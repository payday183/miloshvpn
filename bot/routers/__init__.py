try:
    from bot.routers import user
except ModuleNotFoundError:
    from routers import user


def setup(dp):
    dp.include_router(user.router)
