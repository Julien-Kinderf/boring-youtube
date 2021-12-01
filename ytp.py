from pathlib import Path, PosixPath
from pytube import Channel, YouTube, helpers
from datetime import date,timedelta, datetime
import logging


# Todo: add checks for these files
project_folder = Path(__file__).parent.resolve()
sync_folder = project_folder / "sync"
pool_folder = sync_folder / "pool"
logfile = project_folder / "log"
user_file = project_folder / "users"
archive_file = project_folder / "archive"

# oldest a video can be to be relevant
relevance = timedelta(days=7)
pool_max_size = 5 # in GB
logging.basicConfig(filename=logfile,level=logging.INFO)

def check_files():
    if not archive_file.exists():
        logging.error("No archive file found. Creating a new one")
        archive_file.touch()

def get_users():
    users = user_file.read_text().splitlines()
    if "pool" in users:
        print("No user can be named 'pool'. Aborting")
        logging.critical("No user can be named 'pool'. Aborting.")
        exit(1) # I don't care
    logging.info(f"Retrieved the following users: {users}")
    return(users)

def get_subs(user):
    '''returns pytube channels user is subbed to
    this returns channel_ids'''
    # each user should have a personnal subdir in the sync dir
    user_subdir = sync_folder / user
    if not user_subdir.is_dir():
        logging.info(f"{user} had no subdir. Creating one.")
        user_subdir.mkdir()
    
    # in that personnal subdir, there should be a subs.txt file
    subs_file = user_subdir / "subs.txt"
    if not subs_file.is_file():
        logging.info(f"{user} had no subs.txt file. Creating one.")
        subs_file.touch()

    # there should also be a videos directory
    videos_dir = user_subdir / "videos"
    if not videos_dir.is_dir():
        logging.info(f"{user} had no video directory. Creating one.")
        videos_dir.mkdir()

    # retrieve the list of subscriptions
    # make these pytube Channels
    #subs = [Channel(s).channel_id for s in subs_file.read_text().splitlines()]
    subs = []
    for s in subs_file.read_text().splitlines():
        try:
            subs.append(Channel(s).channel_id)
        except:
            print(f"There was an error with channel {s}. Most probably a typo")
            logging.error(f"There was an error with channel {s}. Most probably a typo. Skipping.")

    logging.info(f"{user} is subscribed to the following channels: {subs}")
    return(subs)


def get_relevant_videos(channel_pool):
    '''
    parameter: a list of channel_ids
    returns: a list of relevant videos (pytube.YouTube type)
    '''
    # Convert the list of channel_ids into pytube.Channels
    channels_list = [Channel(get_url(channel_id)) for channel_id in channel_pool]


    relevant_videos = []
    for c in channels_list:
        i = 0
        relevant = True
        while(relevant and i<len(c)):
            v = YouTube(c[i])
            if is_relevant(v, relevance):
                relevant_videos.append(v)
                relevant = True
                i += 1
            else:
                relevant = False

    logging.info("List  of relevant videos to download:")
    for v in relevant_videos:
        logging.info(f"\t{v.title}")
    return(relevant_videos)


def is_relevant(video, oldest_relevant):
    ''' The criteria is a datetime.timedelta
    This is the maximum age of a relevant video
    '''
    if (date.today() - video.publish_date.date() <= oldest_relevant):
        return(video)
    else:
        return(None)

def download(video, directory, quality=22):
    ''' Downloads the videos in the wanted directory
    the quality parameter can be changed, but 22 gives 720p MP4
    which is a good compromise (and the best quality we can get without DASH
    returns: path to the saved video'''
    stream = video.streams.get_by_itag(quality)
    download_path = stream.download(output_path=pool_folder)
    return(download_path)

def get_url(channel_id):
    return(f"https://youtube.com/channel/{channel_id}")

def get_channel_pool(subs_dict):
    pool=[]
    for user in subs_dict.keys():
        for channel in subs_dict[user]:
            if channel not in pool:
                pool.append(channel)
    return(pool)

def get_subs_dict(users):
    subs_dict = dict.fromkeys(users)
    for u in users:
        subs_dict[u] = get_subs(u)
    return(subs_dict)

def subscribed_users(subs_dict,video):
    '''
    parameter: a dict of form: user:[list of channel_ids]
    parameter: a pytube.YouTube video
    returns: a list of users subscribed to it
        (could be to the channel, but any other rule too)
    '''
    subscribed_users = []
    video_channel_id = video.channel_id
    for user in subs_dict.keys():
        if video_channel_id in subs_dict[user]:
            subscribed_users.append(user)
    return(subscribed_users)

def link_and_download(video,subs_dict):
    '''
    Download the video
    HardLink it to the right user directories
    Archive downloaded videos so that they are not redownloaded after deltion
    '''
    logging.info(f"Trying to download {video.title}")
    # no need if the video had been archived before
    if not is_archived(video.video_id):
        #First download the video
        try:
            video_path = Path(download(video,pool_folder))
            logging.info("\tDownloaded")
            # make the links for users
            for user in subscribed_users(subs_dict,video):
                link_to_make = sync_folder / user / "videos" / video_path.name
                if not(link_to_make.is_file()):
                    logging.info(f"\tLinking for {user}")
                    video_path.link_to(link_to_make)

            # archiving
            archive(video.video_id)
            logging.info("\tArchived")
        except:
            # Just abord and delete the foetus
            # It won't get archived --> downloaded next time
            # It won't get linked --> cleaned next time
            logging.error(f"\tDownload error with {video.title}. Not archived nor linked. Will try next time.")
        

    else:# already archived
        logging.info(f"\tSkipping video because it's already in archive")
    logging.info("")

def archive(video_id):
    with open(archive_file, "a") as f:
        f.write(video_id)
        f.write("\n")

def is_archived(video_id):
    return(video_id in archive_file.read_text().splitlines())

def delete_oldest(users,path):
    # deletes the oldest file from this directory
    #find the oldest file (smallest timestamp)
    ts = datetime.now()
    oldest = None
    
    for dirfile in path.iterdir():
        modtime = datetime.fromtimestamp(dirfile.stat().st_mtime)
        if modtime < ts:
            ts = modtime
            oldest = dirfile

    # to be remembered
    filename = oldest.name
    delete_file(users,oldest)
    return(filename)



def clean(users):
    '''Rules for deletion:
    - delete videos deleted by all users
    - delete irrelevant videos
    - delete old videos untml the pool size < 5GB
    '''
    print(f"Cleaning the pool ({pool_folder}):")
    logging.info("Cleaning the pool ({pool_folder})")
    # iterate on files in pool
    for pool_file in pool_folder.iterdir():
        if deleted_by_all_users(users,pool_file):
            delete_file(users,pool_file)
            print(f"\tDeleted {pool_file.name} from pool. Reason: Deleted by all users")
            logging.warning(f"\tDeleted {pool_file.name} from pool. Reason: Deleted by all users")
        elif too_old(pool_file):
            delete_file(users,pool_file)
            print(f"\tDeleted {pool_file.name} from pool. Reason: too old")
            logging.warning(f"\tDeleted {pool_file.name} from pool. Reason: too old")

    # what if the pool folder is more than the limit ?
    while getsizeof(pool_folder) > pool_max_size * (1024**3): #GB
        name = delete_oldest(users,pool_folder)
        print(f"\tDeleted {name} from pool. Reason: pool size excedded the limit ({pool_max_size}) GB. New size of pool: {round(getsizeof(pool_folder) / (1024**3),2)} GB")
        logging.warning(f"\tDeleted {name} from pool. Reason: pool size excedded the limit ({pool_max_size}) GB. New size of pool: {round(getsizeof(pool_folder) / (1024**3),2)} GB")

    print("The pool is clean")
    logging.info("The pool is clean")
    print()




def delete_file(users, pool_file):
    # delete the file for all users
    for user in users:
        file_to_delete = sync_folder / user / "videos" / pool_file.name
        if file_to_delete.exists():
            file_to_delete.unlink(missing_ok=True)
    # then delete it from the pool
    pool_file.unlink()

def deleted_by_all_users(users,pool_file):
    ret = True
    # check if the file has been deleted by all users
    for user in users:
        user_folder = sync_folder / user / "videos"
        for user_file in user_folder.iterdir():
            if samefile(user_file,pool_file):
                ret = False
    return(ret)

def samefile(path1,path2):
    return(path1.samefile(path2))

def too_old(pool_file):
    ret = True
    last_modification_ts = pool_file.stat().st_mtime
    if date.today() - datetime.fromtimestamp(pool_file.stat().st_mtime).date() < relevance:
        ret = False
    return(ret)


def getsizeof(path):
    path = Path(path)
    return(sum(f.stat().st_size for f in path.glob('**/*') if f.is_file()))


def main():
    start_time = datetime.now()
    logging.info(f"Starting program at {start_time}")
    check_files()
    users = get_users() #str
    clean(users) #cleans pool directory
    subs_dict = get_subs_dict(users) # dict user:[subs]
    channel_pool = get_channel_pool(subs_dict) # list of all channels
    relevant_videos = get_relevant_videos(channel_pool) #list of relevant videos
    for v in relevant_videos:
        link_and_download(v,subs_dict)

    logging.info(f"Program ended after {datetime.now() - start_time}\n")






if __name__ == "__main__":
    main()
