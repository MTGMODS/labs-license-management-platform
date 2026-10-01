import json
import aio_pika
import uuid
import asyncio
from app.shared.config import settings

async def publish_file_generation_event(user_id: int, expire_date: str = None) -> str:
    try:
        connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
    
        async with connection:
            channel = await connection.channel()
            callback_queue = await channel.declare_queue(exclusive=True)
            
            event_dict = {
                "event_type": "FileGenerationRequested",
                "payload": {
                    "user_id": user_id,
                    "expire_date": expire_date
                }
            }
            
            correlation_id = str(uuid.uuid4())
            message = aio_pika.Message(
                body=json.dumps(event_dict).encode("utf-8"),
                correlation_id=correlation_id,
                reply_to=callback_queue.name,
            )

            await channel.default_exchange.publish(message, routing_key="file_generation_queue")
            print(f"[RabbitMQ] Request for build sent. Waiting link for User ID: {user_id}...")

            async with callback_queue.iterator() as queue_iter:
                try:
                    async with asyncio.timeout(30):
                        async for msg in queue_iter:
                            async with msg.process():
                                if msg.correlation_id == correlation_id:
                                    download_url = msg.body.decode("utf-8")
                                    print(f"[RabbitMQ] Received link: {download_url}")
                                    return download_url
                                    
                except asyncio.TimeoutError:
                    raise Exception("Distribution service is busy or offline.")
                    
    except Exception as e:
        print(f"[RabbitMQ] ❌ Error: {e}")
        raise e

async def publish_bot_command(routing_key: str, payload: dict):
    try:
        connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        async with connection:
            channel = await connection.channel()
            
            exchange = await channel.declare_exchange(name="mtgmods.bot.commands", type=aio_pika.ExchangeType.TOPIC, durable=True)

            message_body = json.dumps(payload).encode("utf-8")
            message = aio_pika.Message(body=message_body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
            
            await exchange.publish(message, routing_key=routing_key)
            print(f"[RabbitMQ] Published command '{routing_key}'")
            
    except Exception as e:
        print(f"[RabbitMQ] ❌ Publish Error ({routing_key}): {e}")
        raise