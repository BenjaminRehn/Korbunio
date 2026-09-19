import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:korbunio_app/api/kitchenowl_client.dart';

KitchenOwlClient _client(
  MockClient http, {
  String baseUrl = 'https://kitchenowl.example',
}) => KitchenOwlClient(baseUrl: baseUrl, token: 'secret', httpClient: http);

void main() {
  test('removes the matching entry from the list', () async {
    final calls = <String>[];
    Map<String, Object?>? deleteBody;
    final client = _client(
      MockClient((request) async {
        calls.add('${request.method} ${request.url.path}');
        if (request.method == 'GET') {
          return http.Response(
            jsonEncode([
              {'id': 10, 'name': 'Butter'},
              {'id': 11, 'name': 'Joghurt'},
            ]),
            200,
          );
        }
        deleteBody = jsonDecode(request.body) as Map<String, Object?>;
        expect(request.headers['Authorization'], 'Bearer secret');
        return http.Response('{"msg":"DELETED"}', 200);
      }),
    );

    expect(await client.removeArticle('1', 'Joghurt'), isTrue);
    expect(calls, [
      'GET /api/shoppinglist/1/items',
      'DELETE /api/shoppinglist/1/item',
    ]);
    expect(deleteBody, {'item_id': 11});
  });

  test('reports an article that is already gone without deleting', () async {
    final calls = <String>[];
    final client = _client(
      MockClient((request) async {
        calls.add(request.method);
        return http.Response(
          jsonEncode([
            {'id': 10, 'name': 'Butter'},
          ]),
          200,
        );
      }),
    );

    expect(await client.removeArticle('1', 'Joghurt'), isFalse);
    expect(calls, ['GET']);
  });

  test('finds an entry whose name sits in a nested item', () async {
    Map<String, Object?>? deleteBody;
    final client = _client(
      MockClient((request) async {
        if (request.method == 'GET') {
          return http.Response(
            jsonEncode([
              {
                'id': 7,
                'item': {'id': 7, 'name': 'Milch'},
              },
            ]),
            200,
          );
        }
        deleteBody = jsonDecode(request.body) as Map<String, Object?>;
        return http.Response('{}', 200);
      }),
    );

    expect(await client.removeArticle('1', 'Milch'), isTrue);
    expect(deleteBody, {'item_id': 7});
  });

  test('refuses a list id that is not numeric', () async {
    final client = _client(
      MockClient((request) async => http.Response('[]', 200)),
    );
    expect(
      () => client.removeArticle('1/../../x', 'Milch'),
      throwsA(isA<KitchenOwlException>()),
    );
  });

  test('a rejected token surfaces as a KitchenOwl error', () async {
    final client = _client(
      MockClient((request) async => http.Response('', 401)),
    );
    expect(
      () => client.removeArticle('1', 'Milch'),
      throwsA(isA<KitchenOwlException>()),
    );
  });

  test('never sends the token over plain HTTP', () async {
    var requests = 0;
    final client = _client(
      MockClient((request) async {
        requests++;
        return http.Response('[]', 200);
      }),
      baseUrl: 'http://kitchenowl.example',
    );
    await expectLater(
      client.removeArticle('1', 'Milch'),
      throwsA(isA<KitchenOwlException>()),
    );
    expect(requests, 0);
  });
}
