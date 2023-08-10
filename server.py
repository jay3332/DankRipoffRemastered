from aiohttp import web
from discord.ext.ipc import Client

from config import dbl_secret, ipc_secret

routes = web.RouteTableDef()
ipc = Client(secret_key=ipc_secret)


@routes.get('/')
async def hello(_request: web.Request) -> web.Response:
    return web.Response(text='Hello, world!')


@routes.post('/dbl')
async def dbl(request: web.Request) -> web.Response:
    if request.headers.get('Authorization') != dbl_secret:
        raise web.HTTPUnauthorized()

    data = await request.json()
    await ipc.request('dbl_vote', user_id=int(data['user']), type=data['type'], is_weekend=data['isWeekend'])
    return web.Response()


if __name__ == '__main__':
    app = web.Application()
    app.add_routes(routes)
    web.run_app(app, port=8090)
