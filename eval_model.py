import torch
from torch.autograd import Variable
import pickle as pickle
import argparse
import pdb, os
import numpy as np
import models
from torch.nn.utils import clip_grad_norm
from tqdm import tqdm
import dataloader
from visdom import Visdom
from nltk.tokenize import word_tokenize

parser = argparse.ArgumentParser()

parser.add_argument("--train-file", dest="train_file", help="Path to train datafile", default='finished_files/train.bin', type=str)
parser.add_argument("--test-file", dest="test_file", help="Path to test/eval datafile", default='finished_files/test.bin', type=str)
parser.add_argument("--vocab-file", dest="vocab_file", help="Path to vocabulary datafile", default='finished_files/vocabulary.bin', type=str)

parser.add_argument("--max-abstract-size", dest="max_abstract_size", help="Maximum size of abstract for decoder input", default=110, type=int)
parser.add_argument("--max-article-size", dest="max_article_size", help="Maximum size of article for encoder input", default=300, type=int)
parser.add_argument("--batch-size", dest="batchSize", help="Mini-batch size", default=32, type=int)
parser.add_argument("--embed-size", dest="embedSize", help="Size of word embedding", default=300, type=int)
parser.add_argument("--hidden-size", dest="hiddenSize", help="Size of hidden to model", default=128, type=int)

parser.add_argument("--lambda", dest="lmbda", help="Hyperparameter for auxillary cost", default=1, type=float)
parser.add_argument("--beam-size", dest="beam_size", help="beam size for beam search decoding", default=2, type=int)
parser.add_argument("--max-decode", dest="max_decode", help="Maximum length of decoded output", default=120, type=int)
parser.add_argument("--truncate-vocab", dest="trunc_vocab", help="size of truncated Vocabulary <= 50000 [to save memory]", default=50000, type=int)
parser.add_argument("--bootstrap", dest="bootstrap", help="Bootstrap word embeds with GloVe?", default=0, type=int)
parser.add_argument("--print-ground-truth", dest="print_ground_truth", help="Print the article and abstract", default=1, type=int)

parser.add_argument("--load-model", dest="load_model", help="Directory from which to load trained models", default=None, type=str)
parser.add_argument("--article", dest="article_path", help="Path to article text file", default=None, type=str)

opt = parser.parse_args()
vis = Visdom()

assert opt.load_model is not None and os.path.isfile(opt.vocab_file), 'Invalid Path to trained model file'

def make_html_safe(s):
    """Replace any angled brackets in string s to avoid interfering with HTML attention visualizer."""
    s.replace("<", "&lt;")
    s.replace(">", "&gt;")
    return s

def write_for_rouge(reference_sents, decoded_words, ex_index):
    """Write output to file in correct format for eval with pyrouge. This is called in single_pass mode.
    Args:
      reference_sents: list of strings
      decoded_words: list of strings
      ex_index: int, the index with which to label the files
    """
    # First, divide decoded output into sentences
    decoded_sents = []
    while len(decoded_words) > 0:
      try:
        fst_period_idx = decoded_words.index(".")
      except ValueError: # there is text remaining that doesn't end in "."
        fst_period_idx = len(decoded_words)
      sent = decoded_words[:fst_period_idx+1] # sentence up to and including the period
      decoded_words = decoded_words[fst_period_idx+1:] # everything else
      decoded_sents.append(' '.join(sent))

    # pyrouge calls a perl script that puts the data into HTML files.
    # Therefore we need to make our output HTML safe.
    decoded_sents = [make_html_safe(w) for w in decoded_sents]
    reference_sents = [make_html_safe(w) for w in reference_sents]

    rouge_ref_dir = 'actual_abstract/'
    rouge_dec_dir = 'gen_abstract/'

    # Write to file

    ref_file = os.path.join(rouge_ref_dir, "%06d_reference.txt" % ex_index)
    decoded_file = os.path.join(rouge_dec_dir, "%06d_decoded.txt" % ex_index)

    with open(ref_file, "w") as f:
      for idx,sent in enumerate(reference_sents):
        f.write(sent + '.') if idx==len(reference_sents)-1 else f.write(sent + '.' +"\n")
    with open(decoded_file, "w") as f:
      for idx,sent in enumerate(decoded_sents):
        f.write(sent) if idx==len(decoded_sents)-1 else f.write(sent+"\n")

    # tf.logging.info("Wrote example %i to file" % ex_index)


### utility code for displaying generated abstract
def displayOutput(j, all_summaries, article, abstract, article_oov, show_ground_truth=True):    

    # f1 = open('actual_abstract/' + str(i) + '.txt', 'w+')
    # f2 = open('gen_abstract/' + str(i) +'.txt', 'w+')

    special_tokens = ['<s>','<go>','<end>','</s>']
    print('*' * 150)
    print('\n')
    if show_ground_truth:
        print('ARTICLE TEXT : \n', article)
        list_article = article.split('.')
        print ('ACTUAL ABSTRACT : \n', abstract)
    for i, summary in enumerate(all_summaries):
        if i == 0:
            gen_list = [dl.id2word[ind] if ind<=dl.vocabSize else article_oov[ind % dl.vocabSize] for ind in summary]
            write_for_rouge(list_article, gen_list, j)

        generated_summary = ' '.join([dl.id2word[ind] if ind<=dl.vocabSize else article_oov[ind % dl.vocabSize] for ind in summary])
        for token in special_tokens:
            generated_summary.replace(token, '')
        print ('GENERATED ABSTRACT #%d : \n' %(i+1), generated_summary)   
    print('*' * 150)
    return

# Utility code to save model to disk
def save_model(net, optimizer,all_summaries, article_string, abs_string):
    save_dict = dict({'model': net.state_dict(), 'optim': optimizer.state_dict(), 'epoch': dl.epoch, 'iter':dl.iterInd, 'summaries':all_summaries, 'article':article_string, 'abstract_gold':abs_string})
    print('\n','-' * 60)
    print('Saving Model to : ', opt.save_dir)
    save_name = opt.save_dir + 'savedModel_E%d_%d.pth' % (dl.epoch, dl.iterInd)
    torch.save(save_dict, save_name)
    print('-' * 60)  
    return



assert opt.trunc_vocab <= 50000, 'Invalid value for --truncate-vocab'
assert os.path.isfile(opt.vocab_file), 'Invalid Path to vocabulary file'
with open(opt.vocab_file, 'rb') as f:
    print(f)
    vocab = pickle.load(f, encoding='latin1')                                                          #list of tuples of word,count. Convert to list of words
    vocab = [item[0] for item in vocab[:-(5+ 50000 - opt.trunc_vocab)]]             # Truncate vocabulary to conserve memory
vocab += ['<unk>', '<go>', '<end>', '<s>', '</s>']                                  # add special token to vocab to bring total count to 50k

dl = dataloader.dataloader(opt.batchSize, None, vocab, opt.train_file, opt.test_file, 
                          opt.max_article_size, opt.max_abstract_size, test_mode=True)


wordEmbed = torch.nn.Embedding(len(vocab) + 1, opt.embedSize, 0)
print('Building SummaryNet...')
net = models.SummaryNet(opt.embedSize, opt.hiddenSize, dl.vocabSize, wordEmbed,
                       start_id=dl.word2id['<go>'], stop_id=dl.word2id['<end>'], unk_id=dl.word2id['<unk>'],
                       max_decode=opt.max_decode, beam_size=opt.beam_size, lmbda=opt.lmbda)
net = net.cuda()

print('Loading weights from file...might take a minute...')
saved_file = torch.load(opt.load_model)
net.load_state_dict(saved_file['model'])
print('\n','*'*30, 'LOADED WEIGHTS FROM MODEL FILE : %s' %opt.load_model,'*'*30)
    
############################################################################################
# Set model to eval mode
############################################################################################
net.eval()
print('\n\n')

i = 0
# Run x times to get x random test data samples for output
for _ in range(5):
    i += 1
    # If article file provided
    if opt.article_path is not None and os.path.isfile(opt.article_path):
        with open(opt.article_path,'r') as f:
            article_string = f.read().strip()
            article_tokenized = word_tokenize(article_string)
        _article, _revArticle,  _extArticle, max_article_oov, article_oov = dl.getInputTextSample(article_tokenized)
        abs_string = '**No abstract available**'
    else:
    # pull random test sample
        data_batch = dl.getEvalSample()
        _article, _revArticle,  _extArticle, max_article_oov, article_oov, article_string, abs_string = dl.getEvalSample()

    _article = Variable(_article.cuda(), volatile=True)
    _extArticle = Variable(_extArticle.cuda(), volatile=True)
    _revArticle = Variable(_revArticle.cuda(), volatile=True)    
    all_summaries = net((_article, _revArticle, _extArticle), max_article_oov, decode_flag=True)

    displayOutput(i, all_summaries, article_string, abs_string, article_oov, show_ground_truth=opt.print_ground_truth)


