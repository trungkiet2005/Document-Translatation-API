import requests

url = 'https://huynhtrungkiet09032005-pdf-translate-api.hf.space'
files = {'file': open('./test/file/translate.cli.text.with.figure.pdf', 'rb')}
data = {
    'source_lang': 'en',
    'target_lang': 'vi',
    'service': 'google',
    'threads': '4'
}

response = requests.post(url, files=files, data=data)
result = response.json()
print('Task ID:', result['task_id'])