import discord.utils
from aiohttp import web
from aiohttplimiter import Limiter, default_keyfunc
from discord.ext.ipc import Client

from config import dbl_secret, ipc_secret

routes = web.RouteTableDef()
ipc = Client(secret_key=ipc_secret)
limiter = Limiter(keyfunc=default_keyfunc)


@routes.get('/')
async def hello(_request: web.Request) -> web.Response:
    return web.Response(text='Hello, world!')


@routes.post('/dbl')
async def dbl(request: web.Request) -> web.Response:
    if request.headers.get('Authorization') != dbl_secret:
        raise web.HTTPUnauthorized()

    data = await request.json()
    # documented as isWeekend but is actually is_weekend
    is_weekend = data.get('is_weekend') or data.get('isWeekend') or False
    await ipc.request(
        'dbl_vote',
        user_id=int(data['user']),
        type=data['type'],
        is_weekend=is_weekend,
        voted_at=discord.utils.utcnow().isoformat(),
    )
    return web.Response()


@routes.get('/global')
@limiter.limit('5/4second')
async def global_(_request: web.Request) -> web.Response:
    response = await ipc.request('global_stats')
    return web.json_response(response.response)


@routes.get('/user/{user_id:\\d+}')
@limiter.limit('2/8second')
async def user_data(request: web.Request) -> web.Response:
    user_id = int(request.match_info['user_id'])
    response = await ipc.request('user_data', user_id=user_id)
    return web.json_response(response.response)


if __name__ == '__main__':
    app = web.Application()
    app.add_routes(routes)
    web.run_app(app, port=8090)
