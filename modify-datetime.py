import os
from datetime import datetime

target_dir = './'

for dir_name, _, file_list in os.walk(target_dir, topdown=False):
    for file_name in file_list:
        if file_name[0] == '.' or file_name.split('.').pop() != 'MP4':
            continue
        info = os.stat('./%s' % file_name)
        print(file_name)
        created = datetime.fromtimestamp(info.st_ctime)
        adjusted = created.replace(hour=created.hour + 5)
        print(created)
        print(adjusted)
        os.system('SetFile -d "{}" {}'.format(adjusted.strftime('%m/%d/%Y %H:%M:%S'), file_name))
        os.system('SetFile -m "{}" {}'.format(adjusted.strftime('%m/%d/%Y %H:%M:%S'), file_name))
