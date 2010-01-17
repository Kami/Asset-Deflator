# -*- coding: utf-8 -*-
#
# Name: Asset Deflator
# Description: Script for minifying / compiling / compressing your website static resources.
# Author: Tomaž Muraus (http://www.tomaz-muraus.info)
# Version: 1.0.0
# License: GPL

# Requirements:
# - Linux / FreeBSD / Mac OS
# - Python >= 2.5
# - Java (http://www.java.com/en/download/manual.jsp)
# - YUI Compressor (http://developer.yahoo.com/yui/compressor/)
# - Google Closure Compiler (http://code.google.com/closure/compiler/)
# - jpegoptim (http://freshmeat.net/projects/jpegoptim/)
# - optipng (http://optipng.sourceforge.net/)

__version__ = '1.0.0'

import os
import re
import logging
import optparse
import time
import datetime
import shutil
import tempfile
import operator
import subprocess
import threading

# Path to the external tools / binaries
JAVA_PATH = '/usr/local/bin/java'
YUI_COMPRESSOR_PATH = '/usr/local/bin/yuicompressor.jar'
CLOSURE_COMPILER_PATH = '/usr/local/bin/closure-compiler.jar'
JPEGOPTIM_PATH = '/usr/local/bin/jpegoptim'
OPTIPNG_PATH = '/usr/local/bin/optipng'

class AssetDeflator():
    javascript_re = re.compile('<script type="text/javascript">(.*?)</script>', re.DOTALL | re.IGNORECASE)
    file_name_suffix = '.min'
    
    def __init__(self, assets_path, actions, overwrite_original, print_statistics):
        self.assets_path = assets_path
        self.actions = actions
        self.overwrite_original = overwrite_original
        self.print_statistics = print_statistics
        
        self.files_count = 0
        self.size_before = {'css': 0, 'js': 0, 'inline_js': 0, 'img': 0}
        self.size_after = {'css': 0, 'js': 0, 'inline_js': 0, 'img': 0}
        
        self.__create_temporary_directories()
        
    def start(self):
        """ Start the minification / compilation / compression process. """

        self.start_time = time.time()
        
        workers = []
        for key, value in actions.iteritems():
            if key == 'min_css':
                workers.append(threading.Thread(target = self.minify_css, args = (self.__find_valid_files(self.assets_path, ['css']), )))
            elif key == 'compile_js':
                workers.append(threading.Thread(target = self.compile_javascript, args = (self.__find_valid_files(self.assets_path, ['js']), )))
            elif key == 'compile_inline_js':
                workers.append(threading.Thread(target = self.compile_inline_javascript, args = (self.__find_files_with_inline_javascript(self.__find_valid_files(self.assets_path, ['htm', 'html', 'tpl', 'php', 'asp'])), )))
            elif key == 'compress_imgs':
                workers.append(threading.Thread(target = self.compress_images, args = (self.__find_valid_files(self.assets_path, ['jpg', 'jpeg', 'png', 'gif']), )))
        
        for worker in workers:
            worker.start()
        
        # Wait for all threads to finish        
        for thread in threading.enumerate():
            if thread is not threading.currentThread():
                thread.join()
        
        self.end_time = time.time()
        
        if self.print_statistics:
            self.print_stats()
        
        # Clean up the temporary directories and files created during the compilation process       
        self.__cleanup_tempporary_files()
        
    def minify_css(self, css_files):
        """ Minify CSS files. """
        
        if not css_files:
            return
        
        logging.info('CSS minification: start')
        self.size_before['css'] = self.__calculate_files_size(css_files)
        self.files_count += len(css_files)

        for file in css_files:
            if self.overwrite_original:
                output_file = file
            else:
                output_file = self.__get_file_name_with_suffix(file)
            
            subprocess.Popen('%(java_path)s -jar %(yui_compressor_path)s --type css "%(input_file)s" -o "%(output_file)s"' % {'java_path': JAVA_PATH, 'yui_compressor_path': YUI_COMPRESSOR_PATH, 'input_file': file, 'output_file': output_file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
        
        if self.overwrite_original:
            self.size_after['css'] = self.__calculate_files_size(css_files)
        else:
            self.size_after['css'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, css_files))
        
        logging.info('CSS minification: completed')
                
    def compile_javascript(self, javascript_files):
        """ Compile JavaScript files with Google Closure Compiler. """
        
        if not javascript_files:
            return
        
        logging.info('JavaScript compilation: start')
        self.size_before['js'] = self.__calculate_files_size(javascript_files)
        
        for file in javascript_files:
            if self.overwrite_original:
                output = subprocess.Popen('%(java_path)s -jar %(closure_compiler_path)s --compilation_level SIMPLE_OPTIMIZATIONS --warning_level QUIET --js "%(input_file)s"' % {'java_path': JAVA_PATH, 'closure_compiler_path': CLOSURE_COMPILER_PATH, 'input_file': file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
            
                with open(file, 'w+') as f:
                    f.truncate(0)        
                    f.seek(0)
                    f.write(output)
            else:
                subprocess.Popen('%(java_path)s -jar %(closure_compiler_path)s --compilation_level SIMPLE_OPTIMIZATIONS --warning_level QUIET --js "%(input_file)s" --js_output_file "%(output_file)s"' % {'java_path': JAVA_PATH, 'closure_compiler_path': CLOSURE_COMPILER_PATH, 'input_file': file, 'output_file': self.__get_file_name_with_suffix(file)}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
        
        if self.overwrite_original:
            self.size_after['js'] = self.__calculate_files_size(javascript_files)
        else:
            self.size_after['js'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, javascript_files))
        
        logging.info('JavaScript compilation: completed')
        
    def compile_inline_javascript(self, files):
        """ Compile inline JavaScript with Google Closure Compiler. """
        
        if not files:
            return

        logging.info('Inline JavaScript compilation: start')
        self.size_before['inline_js'] = self.__calculate_files_size(files)
        self.files_count += len(files)
        
        # Copy the files to a temporary directory
        original_file_locations = []
        for index, file in enumerate(files):
            # Index is prepended to the file name, because files can be located in different directories and have the same name
            shutil.copy(file, os.path.join(self.temporaryDirectories['js_files'], str(index) + '.' + os.path.basename(file)))
            original_file_locations.append(os.path.dirname(file))
        
        # Find inline JavaScript, extract it and write it to a temporary file
        javascript_files = {}
        for file in os.listdir(self.temporaryDirectories['js_files']):
            with open(os.path.join(self.temporaryDirectories['js_files'], file), 'r') as f:
                content = f.read()
                matches = self.javascript_re.findall(content)
                
                if len(matches) > 0:
                    javascript_files[file] = []
                        
                for match in matches:
                    tmp_file = tempfile.NamedTemporaryFile(delete = False)
                    tmp_file.write(match)
                    tmp_file.close()

                    javascript_files[file].append(tmp_file.name)
                    self.temporaryFiles.append(tmp_file.name)
        
        # Compile temporary files with JavaScript code and save the compiled code to a temporary file  named <tem_pname>.min.extension
        for key, temp_files in javascript_files.iteritems():
            for file in temp_files:
                output = subprocess.Popen('%(java_path)s -jar %(closure_compiler_path)s --compilation_level SIMPLE_OPTIMIZATIONS --warning_level QUIET --js "%(input_file)s"' % {'java_path': JAVA_PATH, 'closure_compiler_path': CLOSURE_COMPILER_PATH, 'input_file': file}, shell = True, stdout = subprocess.PIPE, close_fds = True).communicate()[0]
             
                with open(file + self.file_name_suffix, 'w+') as js_f:
                    js_f.write(output)
                      
        # Open the files with blocks of inline JavaScript code and replace the non compiled JavaScript with compiled one
        for index, file in enumerate(os.listdir(self.temporaryDirectories['js_files'])):
            
            file_path = os.path.join(self.temporaryDirectories['js_files'], file)
            with open(file_path, 'r+') as f:
                content = f.read()
                
                for javascript_file in javascript_files[file]:
                    with open(javascript_file, 'r') as js_f:
                        original_javascript = js_f.read()
                        
                    with open(javascript_file + self.file_name_suffix, 'r') as js_f:
                        optimized_javascript = js_f.read()
                        
                    content = re.sub('%(original_javascript)s' % {'original_javascript': re.escape(original_javascript)}, optimized_javascript, content, 1)
                    
                f.truncate(0)        
                f.seek(0)
                f.write(content)
                
            # Remove index from the file name, (optionally) add a suffix and move file back to the original location
            file_path = self.__remove_index_from_file_name(file_path)
            
            if not self.overwrite_original:
                file_path = self.__add_suffix_after_file_name(file_path)
                
            self.__move_file(file_path, original_file_locations[index])
 
        if self.overwrite_original:
            self.size_after['inline_js'] = self.__calculate_files_size(files)
        else:
            self.size_after['inline_js'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, files))
            
        logging.info('Inline JavaScript compilation: completed')
        
    def compress_images(self, image_files):
        """ Compress images using jpegoptim / optipng tool. """
        
        if not image_files:
            return
        
        logging.info('Image compression: start')
        self.size_before['img'] = self.__calculate_files_size(image_files)
        self.files_count += len(image_files)

        jpg_image_files = filter(lambda file: True if file.split('.')[-1] in ['jpg', 'jpeg'] else False, image_files)
        png_image_files = filter(lambda file: True if file.split('.')[-1] in ['png', 'gif'] else False, image_files)    
        
        if self.overwrite_original:
            destination = ''
        else:
            destination = '--dest="%(temp_directory)s"' % {'temp_directory': self.temporaryDirectories['jpg_files']}

        for file in jpg_image_files:
            subprocess.Popen('%(jpegoptim_path)s --strip-all %(destination)s "%(input_file)s"' % {'jpegoptim_path': JPEGOPTIM_PATH, 'destination': destination, 'input_file': file}, shell = True, stdout = subprocess.PIPE).communicate()[0]
 
            compressed_file_path = os.path.join(self.temporaryDirectories['jpg_files'], os.path.basename(file))
            if not self.overwrite_original and os.path.exists(compressed_file_path):
                # Add a suffix to the file name and move it back to the original location 
                new_name = self.__add_suffix_after_file_name(compressed_file_path)
                self.__move_file(new_name, os.path.dirname(file))
        
        for file in png_image_files:
            if self.overwrite_original:
                output_file = file
            else:
                output_file = self.__get_file_name_with_suffix(file)
                
            subprocess.Popen('%(optipng_path)s "%(input_file)s" -out "%(output_file)s"' % {'optipng_path': OPTIPNG_PATH, 'input_file': file, 'output_file': output_file}, shell = True, stdout = subprocess.PIPE).communicate()[0]
        
        if self.overwrite_original:
            self.size_after['img'] = self.__calculate_files_size(image_files)
        else:
            self.size_after['img'] = self.__calculate_files_size(map(self.__get_file_name_with_suffix, image_files))
        
        logging.info('Image compression: completed')
        
    def __calculate_files_size(self, files):
        """ Calculate the size of the files in the list. """
        
        return reduce(operator.add, map(lambda file: os.path.getsize(file) if os.path.exists(file) else 0, files))
        
    def __find_files_with_inline_javascript(self, files):
        """ Return a list of files which contain blocks of JavaScript code """
        
        valid_files = []
        for file in files:
            with open(file, 'r') as f:
                content = f.read()
                matches = self.javascript_re.findall(content)
                
                if len(matches) > 0:
                    valid_files.append(file)
                    
        return valid_files
    
    def __find_valid_files(self, path, valid_extensions):
        """ Return a list of files with a valid extension. """

        valid_files = []
        for dir_path, dir_name, file_names in os.walk(path):
            for file in file_names:
                extension = file.split('.')[-1]
                
                # Skip the files with .min suffix so we don't do stuff with already minified / compressed files
                if extension in valid_extensions and file.find(self.file_name_suffix) == -1:
                    valid_files.append(os.path.join(dir_path, file))

        return valid_files
    
    def __move_file(self, path, destination):
        """ Move a file or multiple files to a destination directory. """

        if os.path.isdir(path):
            for dir_path, dir_names, file_names in os.walk(path):
                for file in file_names:
                    file_path = os.path.join(dir_path, file)
                    shutil.move(file_path, os.path.join(destination, os.path.basename(file_path)))
        else:
            shutil.move(path, os.path.join(destination, os.path.basename(path)))
    
    def __add_suffix_after_file_name(self, path):
        """ Add a defined suffix after the file name before the file extension. """
        
        if os.path.isdir(path):
            for dir_path, dir_names, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(dir_path, file)  
                    
                    new_file_name = self.__get_file_name_with_suffix(file_path)
                    shutil.move(file_path, new_file_name)
        else:
            new_file_name = self.__get_file_name_with_suffix(path)      
            shutil.move(path, new_file_name)
            
            return new_file_name
        
    def __remove_index_from_file_name(self, path):
        """ Remove index from the beginning of the file name. """
        
        new_file_name = path[path.find('.') + 1:]
        shutil.move(path, new_file_name)    
        
        return new_file_name
            
    def __get_file_name_with_suffix(self, file_name):
        """ Return file name with added suffix. """
        
        (name, extension) = os.path.splitext(file_name)
        new_name = name + self.file_name_suffix + extension
            
        return new_name 
    
    def __create_temporary_directories(self):
        """ Create temporary directories. """
        
        self.temporaryDirectories = {}
        self.temporaryFiles = []
        
        self.temporaryDirectories['js_files'] = tempfile.mkdtemp()
        self.temporaryDirectories['jpg_files'] = tempfile.mkdtemp()
        self.temporaryDirectories['png_files'] = tempfile.mkdtemp()
        
    def __cleanup_tempporary_files(self):
        """ Delete all temporary directories and files. """
        
        for key, directory in self.temporaryDirectories.iteritems():
            if os.path.exists(directory):
                shutil.rmtree(directory, ignore_errors = True)
            
        for file in self.temporaryFiles:
            if os.path.exists(file):
                os.remove(file)
    
    def print_stats(self):
        print 'Statistics'
        print ''
        
        if self.files_count == 0:
            print 'Found 0 files to work on in %(assets_path)s'  % {'assets_path': self.assets_path}
        else:
            print 'Performed work on %(files_count)d files located in %(assets_path)s'  % {'files_count': self.files_count, 'assets_path': self.assets_path}
            
        print 'Running time: %(running_time)s' % {'running_time': str(datetime.timedelta(seconds = int(self.end_time - self.start_time)))}
        
        if 'min_css' in self.actions and self.size_before['css'] > 0:
            print ''
            print 'CSS files:'
            print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['css'], 'size_after': self.size_after['css'], 'difference': operator.neg((100 - ((float(self.size_after['css']) / self.size_before['css']) * 100)))}
            
        if 'compile_js' in self.actions and self.size_before['js'] > 0:
            print ''
            print 'JavaScript files:'
            print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['js'], 'size_after': self.size_after['js'], 'difference': operator.neg((100 - (float(self.size_after['js']) / self.size_before['js'] * 100)))}
            
        if 'compile_inline_js' in self.actions  and self.size_before['inline_js'] > 0:
            print ''
            print 'JavaScript inline:'
            print 'Size before: %(size_before)d bytes, Size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['inline_js'], 'size_after': self.size_after['inline_js'], 'difference': operator.neg((100 - (float(self.size_after['inline_js']) / self.size_before['inline_js'] * 100)))}
            
        if 'compress_imgs' in self.actions  and self.size_before['img'] > 0:
            print ''
            print 'Image files:'
            print 'Size before: %(size_before)d bytes, size after: %(size_after)d bytes %(difference)+.2f%%' % {'size_before': self.size_before['img'], 'size_after': self.size_after['img'], 'difference': operator.neg((100 - (float(self.size_after['img']) / self.size_before['img'] * 100)))}
    
if __name__ == '__main__':
    parser = optparse.OptionParser(version = '%prog ' + __version__)
    parser.add_option('-v', '--verbose', action = 'store_true', default = False, dest = 'verbose', help = 'print what is going on [default: %default]')
    parser.add_option('-o', '--overwrite', action = 'store_true', default = False, dest = 'overwrite_original', help = 'overwrite the original files (don\'t create new files with .min extension) [default: %default]')
    parser.add_option('-s', '--statistics', action = 'store_true', default = False, dest = 'print_statistics', help = 'print statistics at the end')
    parser.add_option('--path', action = 'store', type = 'string', dest = 'assets_path', metavar = 'PATH', help = 'path to your asset files')
    parser.add_option('--all', action = 'store_true', default = False, dest = 'run_all', help = 'run all actions')
    
    parser.add_option('--minify-css', action = 'store_true', default = False, dest = 'action_min_css', help = 'minify the CSS files')
    parser.add_option('--compile-js', action = 'store_true', default = False, dest = 'action_compile_js', help = 'compile JavaScript files using Google Closure Compiler')
    parser.add_option('--compile-inline-js', action = 'store_true', default = False, dest = 'action_compile_inline_js', help = 'find and compile inline blocks of JavaScript code using Google Closure Compiler')
    parser.add_option('--compress-images', action = 'store_true', default = False, dest = 'action_compress_imgs', help = 'compress image files')
    
    (options, args) = parser.parse_args()
    options = vars(options)

    if not options['assets_path']:
        parser.error('you must supply location of your assets')
        
    if options['run_all']:
        actions = dict([(key[key.find('_') + 1:], True) for key, value in options.iteritems() if key.find('action_') != -1])
    else:
        actions = dict([(key[key.find('_') + 1:], value) for key, value in options.iteritems() if key.find('action_') != -1 and value != False])
    
    if not actions:
        parser.error('you must supply at least one action')
    
    # Set up logging    
    logging.basicConfig(level = logging.INFO if options['verbose'] else logging.ERROR, format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt = '%d.%m.%Y %H:%M:%S')

    asset_deflator = AssetDeflator(options['assets_path'], actions, options['overwrite_original'], options['print_statistics'])
    asset_deflator.start()