#
import HTMLParser
import json
import os
import re
import urllib
import argparse
import flickrapi
import tempfile
import sys
from gi.repository import GExiv2
from PythonMagick import Image

__author__ = 'faisal'

reload(sys)
sys.setdefaultencoding("utf-8")

EXT_IMAGE = ('jpg', 'jpeg', 'png', 'jpeg', 'gif', 'bmp')
EXT_VIDEO = ('avi', 'wmv', 'mov', 'mp4', '3gp', 'ogg', 'ogv', 'mts')
EXT_CONVERT_IMAGE = ('cr2', 'orf', 'psd', 'tiff', 'tif')


def start_sync(sync_path, cmd_args):
    is_windows = os.name == 'nt'
    is_download = cmd_args.download

    # Put your API & SECRET keys here
    KEY = 'f7da21662566bc773c7c750ddf7030f7'
    SECRET = 'c329cdaf44c6d3f3'

    if not os.path.exists(sync_path):
        print 'Sync path does not exists: ' + sync_path
        exit(0)

    # Common arguments
    args = {'format': 'json', 'nojsoncallback': 1}
    api = flickrapi.FlickrAPI(KEY, SECRET)
    # api.token.path = 'flickr.token.txt'

    # Ask for permission
    (token, frob) = api.get_token_part_one(perms='write')

    if not token:
        raw_input("Please authorized this app then hit enter:")

    try:
        token = api.get_token_part_two((token, frob))
    except:
        print 'Please authorized to use'
        exit(0)

    args.update({'auth_token': token})

    # Build your local photo sets
    photo_sets = {}
    skips_root = []
    for r, dirs, files in os.walk(sync_path):
        files = [f for f in files if not f.startswith('.')]
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        if cmd_args.include_pattern:
            m = re.match(cmd_args.include_pattern, r.replace(sync_path, ''))
            if not m:
                print 'Folder %s does not match pattern %s. Skipping...' % (r, cmd_args.include_pattern)
                continue
            else:
                print 'Folder %s matches pattern %s. Adding to process list...' % (r, cmd_args.include_pattern)

        for file in files:
            if not file.startswith('.'):
                ext = file.lower().split('.').pop()
                if ext in EXT_IMAGE or \
                   ext in EXT_VIDEO or \
                   (cmd_args.handle_raw and ext in EXT_CONVERT_IMAGE):

                    if r == sync_path:
                        skips_root.append(file)
                    else:
                        photo_sets.setdefault(r, [])
                        photo_sets[r].append(file)

    if skips_root:
        print 'To avoid disorganization on flickr sets root photos are not synced, skipped these photos:', skips_root
        print 'Try to sync at top most level of your photos directory'

    # custom set builder
    def get_custom_set_title(path, sync_path):
        title = path[len(sync_path):]

        if cmd_args.custom_set:
            m = re.match(cmd_args.custom_set, path)
            if m:
                if not cmd_args.custom_set_builder:
                    title = '-'.join(m.groups())
                elif m.groupdict():
                    title = cmd_args.custom_set_builder.format(**m.groupdict())
                else:
                    title = cmd_args.custom_set_builder.format(*m.groups())
        return title

    # Get your photosets online and map it to your local
    html_parser = HTMLParser.HTMLParser()
    photosets_args = args.copy()
    page = 1
    photo_sets_map = {}

    # Show 3 possibilities
    if cmd_args.custom_set:
        for photo_set in photo_sets:
            print 'Set Title: [%s]  Path: [%s]' % (get_custom_set_title(photo_set, sync_path), photo_set)

        if raw_input('Is this your expected custom set titles (y/n):') != 'y':
            exit(0)

    while True:
        print 'Getting photosets page %s' % page
        photosets_args.update({'page': page, 'per_page': 500})
        sets = json.loads(api.photosets_getList(**photosets_args))
        page += 1
        if not sets['photosets']['photoset']:
            break

        for set in sets['photosets']['photoset']:
            # Make sure it's the one from backup format
            desc = html_parser.unescape(set['description']['_content'])
            if desc:
                photo_sets_map[desc] = set['id']
                title = get_custom_set_title(sync_path + desc, sync_path)
                if cmd_args.update_custom_set and str(sync_path + desc) in photo_sets and title != set['title']['_content']:
                    update_args = args.copy()
                    update_args.update({
                        'photoset_id': set['id'],
                        'title': title,
                        'description': desc
                    })
                    print 'Updating custom title [%s]...' % title
                    json.loads(api.photosets_editMeta(**update_args))
                    print 'done'

    print 'Found %s photo sets' % len(photo_sets_map)

    # For adding photo to set
    def add_to_photo_set(photo_id, folder):
        # If photoset not found in online map create it else add photo to it
        # Always upload unix style
        if is_windows:
            folder = folder.replace(os.sep, '/')

        flickr_photo_set = None
        for k, v in photo_sets_map.iteritems():
            if folder == str(k):
                flickr_photo_set = k
                break

        if not flickr_photo_set:
            photosets_args = args.copy()
            custom_title = get_custom_set_title(sync_path + folder, sync_path)
            photosets_args.update({'primary_photo_id': photo_id,
                                   'title': custom_title,
                                   'description': folder})
            set = json.loads(api.photosets_create(**photosets_args))
            photo_sets_map[folder] = set['photoset']['id']
            print 'Created set [%s] and added photo' % custom_title
        else:
            photosets_args = args.copy()
            photosets_args.update({'photoset_id': photo_sets_map.get(flickr_photo_set), 'photo_id': photo_id})
            result = json.loads(api.photosets_addPhoto(**photosets_args))
            if result.get('stat') == 'ok':
                print 'Success'
            else:
                print result

    # Get photos in a set
    def get_photos_in_set(folder):
        # bug on non utf8 machines dups
        folder = folder.decode('utf-8')

        photos = {}
        # Always upload unix style
        if is_windows:
            folder = folder.replace(os.sep, '/')

        if folder in photo_sets_map:
            photoset_args = args.copy()
            page = 1
            while True:
                photoset_args.update({'photoset_id': photo_sets_map[folder], 'page': page})
                if is_download:
                    photoset_args['extras'] = 'url_o,media'
                page += 1
                photos_in_set = json.loads(api.photosets_getPhotos(**photoset_args))
                if photos_in_set['stat'] != 'ok':
                    break

                for photo in photos_in_set['photoset']['photo']:

                    if is_download and photo.get('media') == 'video':
                        # photo_args = args.copy()
                        # photo_args['photo_id'] = photo['id']
                        # sizes = json.loads(api.photos_getSizes(**photo_args))
                        # if sizes['stat'] != 'ok':
                        #     continue
                        #
                        # original = filter(lambda s: s['label'].startswith('Site') and s['media'] == 'video', sizes['sizes']['size'])
                        # if original:
                        #     photos[photo['title']] = original.pop()['source'].replace('/site/', '/orig/')
                        #     print photos
                        # Skipts download video for now since it doesn't work
                        continue
                    else:
                        photos[photo['title']] = photo['url_o'] if is_download else photo['id']

        return photos

    def file_eligable_for_conversion(filename):
        if filename.split('.').pop().lower() in EXT_CONVERT_IMAGE:
            return True
        return False

    def convert_local_filename_to_flickr(filename):
        return '.'.join(filename.split('.')[:-1] + ['jpeg'])

    def convert_photo_to_jpeg(photo_path):
        print 'Converting %s to JPEG...' % (photo_path,)
        image = Image(photo_path)
        image.magick("jpeg")
        image.quality(100)
        (_, file_name) = os.path.split(photo_path)
        file_name = convert_local_filename_to_flickr(file_name)
        file_name = os.path.join(tempfile.mkdtemp('.flickrsmartsync'), file_name)
        image.write(file_name)

        print 'Done converting photo!'
        return file_name

    # If download mode lets skip upload but you can also modify this to your needs
    if is_download:
        # Download to corresponding paths
        os.chdir(sync_path)

        for photo_set in photo_sets_map:
            if photo_set and is_download == '.' or is_download != '.' and photo_set.startswith(is_download):
                folder = photo_set.replace(sync_path, '')
                print 'Getting photos in set [%s]' % folder
                photos = get_photos_in_set(folder)
                # If Uploaded on unix and downloading on windows & vice versa
                if is_windows:
                    folder = folder.replace('/', os.sep)

                if not os.path.isdir(folder):
                    os.makedirs(folder)

                for photo in photos:
                    # Adds skips
                    if cmd_args.ignore_images and photo.split('.').pop().lower() in EXT_IMAGE:
                        continue
                    elif cmd_args.ignore_videos and photo.split('.').pop().lower() in EXT_VIDEO:
                        continue

                    path = os.path.join(folder, photo)
                    if os.path.exists(path):
                        print 'Skipped [%s] already downloaded' % path
                    else:
                        print 'Downloading photo [%s]' % path
                        urllib.urlretrieve(photos[photo], os.path.join(sync_path, path))
    else:
        # Loop through all local photo set map and
        # upload photos that does not exists in online map
        for photo_set in sorted(photo_sets):
            folder = photo_set.replace(sync_path, '')
            display_title = get_custom_set_title(photo_set, sync_path)
            print 'Getting photos in set [%s]' % display_title
            photos = get_photos_in_set(folder)
            print 'Found %s photos' % len(photos)

            for photo in sorted(photo_sets[photo_set]):
                #Convert local photo name to online photo name (for example CR2 -> jpeg)
                original_photo = photo
                if file_eligable_for_conversion(photo):
                    photo = convert_local_filename_to_flickr(photo)

                # Adds skips
                if cmd_args.ignore_images and photo.split('.').pop().lower() in EXT_IMAGE:
                    continue
                elif cmd_args.ignore_videos and photo.split('.').pop().lower() in EXT_VIDEO:
                    continue

                photo_exist = False
                for k, v in photos.iteritems():
                    if photo == str(k) or is_windows and photo.replace(os.sep, '/') == str(k):
                        photo_exist = True

                if photo_exist == True:
                    print 'Skipped [%s] already exists in set [%s]' % (photo, display_title)
                else:
                    print 'Uploading [%s] to set [%s]' % (photo, display_title)
                    upload_args = {'auth_token': token, 'title': photo, 'hidden': 1, 'is_public': 0, 'is_friend': 0, 'is_family': 0}

                    file_path = os.path.join(photo_set, photo)
                    if file_eligable_for_conversion(original_photo):
                        file_path = convert_photo_to_jpeg(os.path.join(photo_set, original_photo))

                    file_stat = os.stat(file_path)

                    if file_stat.st_size >= 1073741824:
                        print 'Skipped [%s] over size limit' % photo
                        continue

                    try:
                        upload = api.upload(file_path, None, **upload_args)
                        photo_id = upload.find('photoid').text
                        add_to_photo_set(photo_id, folder)
                        photos[photo] = photo_id
                    except flickrapi.FlickrError as e:
                        print e.message
                    except:
                        # todo add tracking to show later which ones failed
                        pass
                    #TODO Delete any temporary created jpeg files

    print 'All Synced'


def main():
    parser = argparse.ArgumentParser(description='Sync current folder to your flickr account.')
    parser.add_argument('--download', type=str, help='download the photos from flickr specify a path or . for all')
    parser.add_argument('--ignore-videos', action='store_true', help='ignore video files')
    parser.add_argument('--ignore-images', action='store_true', help='ignore image files')
    parser.add_argument('--sync-path', type=str, default=os.getcwd(),
                        help='specify the sync folder (default is current dir)')
    parser.add_argument('--custom-set', type=str, help='customize your set name from path with regex')
    parser.add_argument('--custom-set-builder', type=str, help='build your custom set title (default just merge groups)')
    parser.add_argument('--update-custom-set', action='store_true', help='updates your set title from custom set')
    parser.add_argument('--handle-raw', action='store_true', help='handle raw image files')
    parser.add_argument('--include-pattern', type=str, help='only include folders matching regex')

    args = parser.parse_args()
    start_sync(args.sync_path.rstrip(os.sep) + os.sep, args)
