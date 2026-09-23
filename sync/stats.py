"""Minimal protobuf messages for the sing-box V2Ray StatsService (no reset)."""
import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def messages():
    schema = descriptor_pb2.FileDescriptorProto(name='singbox_stats.proto', package='xbs', syntax='proto3')
    for name, fields in (
        ('Query', [('pattern', 1, 9, False, None), ('reset', 2, 8, False, None)]),
        ('Stat', [('name', 1, 9, False, None), ('value', 2, 3, False, None)]),
        ('Response', [('stat', 1, 11, True, '.xbs.Stat')]),
    ):
        message = schema.message_type.add(name=name)
        for field, number, kind, repeated, ref in fields:
            item = message.field.add(name=field, number=number, type=kind, label=3 if repeated else 1)
            if ref:
                item.type_name = ref
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    return [message_factory.GetMessageClass(pool.FindMessageTypeByName('xbs.' + n))
            for n in ('Query', 'Response')]


Query, Response = messages()


def query():
    with grpc.insecure_channel('127.0.0.1:10086') as channel:
        call = channel.unary_unary('/v2ray.core.app.stats.command.StatsService/QueryStats',
                                  request_serializer=Query.SerializeToString,
                                  response_deserializer=Response.FromString)
        response = call(Query(pattern='user>>>', reset=False), timeout=10)
        return {item.name: item.value for item in response.stat if item.name.startswith('user>>>')}
