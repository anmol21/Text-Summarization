# import rouge
import os
import time
import json
import pyrouge
# from pyrouge import ROUGE155
import numpy as np

ref_dir = 'actual_abstract'
dec_dir = 'gen_abstract'
dir_to_write = 'results'

def rouge_eval(ref_dir, dec_dir):
	
	"""Evaluate the files in ref_dir and dec_dir with pyrouge, returning results_dict"""
	r = pyrouge.Rouge155()
	r.model_filename_pattern = '#ID#_reference.txt'
	r.system_filename_pattern = '(\d+)_decoded.txt'
	r.model_dir = ref_dir
	r.system_dir = dec_dir

	rouge_results = r.convert_and_evaluate()
	return r.output_to_dict(rouge_results)


def rouge_log(results_dict, dir_to_write):
	"""Log ROUGE results to screen and write to file.
	Args:
	results_dict: the dictionary returned by pyrouge
    dir_to_write: the directory where we will write the results to"""
	
	log_str = ""
	for x in ["1","2","l"]:
		log_str += "\nROUGE-%s:\n" % x
	
	for y in ["f_score", "recall", "precision"]:
		key = "rouge_%s_%s" % (x,y)
		key_cb = key + "_cb"
		key_ce = key + "_ce"
		val = results_dict[key]
		val_cb = results_dict[key_cb]
		val_ce = results_dict[key_ce]
		log_str += "%s: %.4f with confidence interval (%.4f, %.4f)\n" % (key, val, val_cb, val_ce)

	print(log_str) # log to screen
	
	results_file = os.path.join(dir_to_write, "ROUGE_results.txt")
	
	print("Writing final ROUGE results to %s...", results_file)
	
	with open(results_file, "w") as f:
		f.write(log_str)

rouge_log(rouge_eval(ref_dir, dec_dir), dir_to_write)