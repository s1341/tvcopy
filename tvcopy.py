#!/usr/bin/env python
import os
import re
import fnmatch
import json
import shutil


SORTED_BASE = "/mnt/permanent/sorted/series"
JUSTARRIVED_BASE = "/mnt/permanent/JustArrived"

global_shows = {}


def file_is_video(filename):
    return not filename.startswith('.') and \
        os.path.splitext(filename)[1].lower() in ['.avi', '.mp4', '.mkv']


def iglob(pattern, directory=None, as_tuple=False):
    if not directory:
        directory, pattern = os.path.split(pattern)
    regpattern = re.compile(fnmatch.translate(pattern), re.IGNORECASE)
    res = []
    for f in os.listdir(directory):
        if regpattern.match(f):
            if as_tuple:
                res.append((directory, f))
            else:
                res.append(os.path.join(directory, f))
    return res


def list_all_video_files(directory=None, pattern=None):
    results = []
    if directory:
        for root, dirs, files in os.walk(directory):
            if ".AppleDouble" in root or ".Trash" in root:
                continue
            for file in files:
                if file_is_video(file):
                    results.append((root, file))
    if pattern:
        for file in iglob(pattern):
            root, file = os.path.split(file)
            if file_is_video(file):
                results.append((root, file))
    return results


class Show:
    def __init__(self, name, origname=""):
        self.name = name
        self.origname = origname
        self.directory = self.get_sorted_directory()
        self.episodes = None

    @classmethod
    def get_show(cls, episode_name):
        orig_name = episode_name
        normalized_name = Show.normalize_name(episode_name)
        show = global_shows.get(normalized_name)
        if not show:
            show = Show(normalized_name, orig_name)
        if show.name == show.origname and show.name != orig_name:
            show.origname = orig_name
            global_shows[normalized_name] = show
            return show

    @classmethod
    def normalize_name(cls, name):
        # normalize the name of the show, getting rid of dots,
        # year markers, country adaptations and title-izing
        name = name.replace(".", " ").title()
        while True:
            last_component = name.split(" ")[-1]
            if last_component.startswith("20") or last_component.upper() in ['US', 'UK']:
                name = " ".join(name.split(" ")[:-1])
            else:
                break
        return name

    def get_sorted_directory(self):
        res = iglob(os.path.join(SORTED_BASE, self.name))
        if res:
            return res[0]
        return None

    def get_all_episodes(self):
        if not self.episodes:
            self.episodes = []
            self.add_episodes(list_all_video_files(self.get_sorted_directory()))
            self.add_episodes(iglob(os.path.join(JUSTARRIVED_BASE,
                                                 self.origname.replace(' ', '.') + ".S*"),
                                    as_tuple=True))
            self.episodes = sorted(self.episodes)
        return self.episodes

    def add_episodes(self, filelist):
        for path, file in filelist:
            if not file_is_video(file):
                continue
            ep = Episode.create(path, file)
            if ep:
                self.episodes.append(ep)

    def get_episode_from_nums(self, season, episode):
        episodes = self.get_all_episodes()
        for ep in episodes:
            if (ep.season, ep.episode) == (season, episode):
                return ep

    def count(self):
        return len(self.get_all_episodes())

    def get_episodes_after(self, episode, inclusive=False):
        episodes = self.get_all_episodes()
        if episode in episodes:
            index = episodes.index(episode)
            return episodes[index + (0 if inclusive else 1):]
        print "ERROR: something went wrong gathering episodes for %s after episode %s" \
            % (self.name, episode)
        return []


class Episode:
    file_re = re.compile(("(?P<showname>[a-zA-z._0-9]+)\.[sS]"
                          "(?P<season>[0-9]+)[eE](?P<episode>[0-9]+).*"))

    def __init__(self, path, filename, showname, season, episode):
        self.filename, self.path = filename, path
        self.season, self.episode = int(season), int(episode)
        self.show = Show.get_show(showname)

    def __repr__(self):
        return "%s [%02d:%02d] %s/%s" % (self.show.name, self.season, self.episode,
                                         self.path, self.filename)

    def _get_sort_sequence(self):
        return self.show.name, self.season, self.episode

    def __eq__(self, other):
        return self._get_sort_sequence() == other._get_sort_sequence()

    def __lt__(self, other):
        return self._get_sort_sequence() < other._get_sort_sequence()

    def __hash__(self):
        return hash(self.path + self.filename)

    def get_path(self):
        return os.path.join(self.path, self.filename)

    @classmethod
    def create(cls, path, filename):
        m = Episode.file_re.match(filename)
        if m:
            return Episode(path, filename, *m.groups())
        return None


def crawl(path):
    filelist = list_all_video_files(directory=path)
    eps = []
    for path, file in filelist:
        ep = Episode.create(path, file)
        if ep:
            eps.append(ep)
    return eps


def get_last_copied(episode_list):
    showlist = {}
    for ep in episode_list:
        showname = ep.show.name
        if showname not in showlist or showlist[showname] < ep:
            showlist[showname] = ep
    return showlist.values()


def get_eps_from_cache(path):
    print "GETFROMCAHCE"
    eps = []
    cache = json.loads(open(path).read())
    for showname, ep_num in cache['last_copied_episodes'].items():
        ep = Show.get_show(showname).get_episode_from_nums(ep_num['season'],
                                                           ep_num['episode'])
        eps.append(ep)
    return eps


def write_cache(path, copylist):
    if os.path.exists(path):
        cache = json.loads(open(path).read())
    else:
        cache = {'last_copied_episodes': {}}
    for showname in sorted(copylist.shows.keys()):
        show = copylist.shows[showname]
        if show.last_copied_epnum:
            # we succesfully copied at least some episodes
            s, e = show.last_copied_epnum
        else:
            # assume the worst. We need the whole sequence
            s, e = show.startep
    open(path, "w").write(json.dumps(cache))


class ShowInfo:
    def __init__(self, show):
        self.show = show
        self.episodes = []
        self.last_copied_epnum = None
        self.last_copied_episode = None
        self.inclusive = False

    def set_last_copied(self, episode, inclusive=False):
        ep_num = episode.season, episode.episode
        if self.last_copied_epnum:
            self.last_copied_epnum = max(self.last_copied_epnum, ep_num)
        else:
            self.last_copied_epnum = ep_num
        if self.last_copied_epnum == ep_num:
            self.last_copied_episode = episode
        self.inclusive = inclusive

    def gather_required_episodes(self):
        print "GATHER:", self.last_copied_episode
        self.episodes = self.show.get_episodes_after(self.last_copied_episode,
                                                     self.inclusive)
        return self.episodes

    def get_most_recent(self):
        if len(self.episodes):
            return self.episodes[-1]
        return self.last_copied_episode

    def count(self):
        return len(self.episodes)

    def __repr__(self):
        most_recent = self.get_most_recent()
        return "%s - last-copied: S%02dE%02d, most-recent: S%02dE%02d (%d files)" \
            % (self.show.name, self.last_copied_epnum[0], self.last_copied_epnum[1],
               most_recent.season, most_recent.episode, self.count())

    def copy(self, outdir, pretend=False):
        for ep in self.episodes:
            if not pretend:
                try:
                    print "[*] copying %s to %s" % (ep.get_path(), outdir)
                    shutil.copy2(ep.get_path(), args.outdir)
                    self.set_last_copied(ep)
                except Exception, e:
                    print e
                    # cleanup partial files
                    os.remove(os.path.join(outdir, os.path.split(ep.get_path())[1]))
                    raise


class EpisodeList:
    def __init__(self):
        self.list = []
        self.shows = {}

    def set_last_copied_episodes(self, eps, inclusive=False):
        for e in eps:
            if e.show.name not in self.shows:
                self.shows[e.show.name] = ShowInfo(e.show)
            self.shows[e.show.name].set_last_copied(e, inclusive)

    def gather_required_episodes(self):
        for show in self.shows.values():
            eps = show.gather_required_episodes()
            self.list += eps

    def has_show(self, name):
        return name in self.shows

    def count(self):
        return len(self.list)

    def __repr__(self):
        return self.display(all=True)

    def display(self, all=False):
        res = ""
        for show in sorted(self.shows.keys()):
            if all or self.shows[show].count():
                res += "\t" + repr(self.shows[show]) + "\n"
        return res

    def copy(self, outdir, pretend=False):
        for showname in sorted(self.shows.keys()):
            try:
                self.shows[showname].copy(outdir, pretend)
            except:
                return


# TODO: make cache a class

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='manage show copying')
    parser.add_argument('dirs', metavar='DIRS', type=str, nargs='*', help='directory to' +
                        ' crawl in order to determine the last_copied episodes copied.')
    parser.add_argument('--pretend', "-p", dest='pretend', action='store_const',
                        const=bool, default=False,
                        help=("don't actually perform any copies, just show what would"
                              " be done"))
    parser.add_argument('--cachefile', "-c", dest='cache_file', default="./show_cache",
                        help=('cache file to consider when determining copy list.'
                              ' if none is specified, will look for "show_cache" in'
                              ' local directory'))
    parser.add_argument('--outdir', "-o", dest='outdir',
                        required=True,
                        help='the directory into which the files will be copied')
    parser.add_argument('--new', "-n", dest='new_shows', action='append',
                        metavar="SHOWNAME",
                        help=('add a new show which should be copied despite not being'
                              ' found in the directory crawl or cache-list.'))
    parser.add_argument('--specific', "-S", dest='specified', action='append', nargs=3,
                        help=('add a specific show with the specified season and ep'
                              ' numbers. the result will include the specifier'))

    args = parser.parse_args()
    last_copied_from_files = []

    copylist = EpisodeList()

    # always append the outdir to the crawl dirs,
    # this should save on double copying
    args.dirs.append(args.outdir)

    if os.path.exists(args.cache_file):
        print "[-] Reading cache file %s" % args.cache_file
        copylist.set_last_copied_episodes(get_eps_from_cache(args.cache_file))

    if args.dirs:
        eps = []
        for dir in args.dirs:
            print "[-] Crawling %s" % dir
            eps += crawl(dir)
        copylist.set_last_copied_episodes(get_last_copied(eps))

    if args.new_shows:
        print "[-] Adding new shows"
        eps = []
        for new in args.new_shows:
            # if we've discovered episodes for this show via the crawl or cache,
            # it's not really new, so we shouldn't include all data
            if not copylist.has_show(Show.get_show(new).name):
                eps.append(Show.get_show(new).get_episode_from_nums(1, 1))
        copylist.set_last_copied_episodes(eps, inclusive=True)

    if args.specified:
        print "[-] Adding specified shows"
        eps = []
        for specifier in args.specified:
            showname, season, epnum = specifier[0], int(specifier[1]), int(specifier[2])
            eps.append(Show.get_show(showname).get_episode_from_nums(season, epnum))
            # this is a special case, the specifier is INCLUSIVE, so 9,1 is 9,1-9,22
        copylist.set_last_copied_episodes(eps)

    print "[*] preparing copy list ..."
    copylist.gather_required_episodes()
    print "[-] Show status list:"
    print repr(copylist)
    print "[-] planning to copy %d files total:" % copylist.count()
    print copylist.display()

    copylist.copy(args.outdir, args.pretend)

    if not args.pretend:
        print "[-] writing cache_file to %s" % args.cache_file
        write_cache(args.cache_file, copylist)

print "[-] DONE"
