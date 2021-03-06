'''This module preprocesses the
output of the extractor to ready it
for the later stages of the summarizer
'''

import re

from nltk.tokenize import sent_tokenize, word_tokenize
from sentence_transformers import SentenceTransformer

from data_models.contents import ContentType
from data_models.preprocessed_contents import PreprocessedContents
from summarization.sentence_with_attributes import SentenceWithAttributes
from summarization.text_summarization import TextSummarizer
from summarization.web_entity_detection import ImageDescriptionRetriever


class ExtractorOutputPreprocessor:
    ''' Class to implement the utilities for
    preprocessing the extractor output

    Preprocessing steps applied:
    * Split contents into different types
    * Assign img_description_embeddings to media
    * Summarize the text content
    '''
    # this limit is based on how many characters
    # we can display as title so it is readable
    # and still does not block out other content
    MAX_TITLE_LENGTH = 100

    def __init__(self, contents):
        self.content_list = contents.content_list
        self.normal_text_content_list = list()  # non-title text
        self.title_text_content_list = list()  # title text
        self.media_content_list = list()  # images/gifs
        self.embedded_content_list = list()  # insta/tweets/quotes
        self.quoted_content_list = list()
        self.text_summarizer = TextSummarizer(priority="accuracy")
        self.sentence_embedding_model \
            = SentenceTransformer('bert-base-nli-stsb-mean-tokens')

    def get_preprocessed_content(self):
        ''' Pre-processes the content
        by splitting it into different content
        types and returns it as a dict
        Return type : dict
        '''
        #  first split content into different categories
        self._split_content()

        # strip numbering from title text
        self._strip_numbering_from_title_text()

        # summarize the webpage text
        self._summarize_text_content()

        # assemble the sentence objects
        self._make_sentence_objects()

        # add image description embeddings
        self._add_media_description_and_attribute_embeddings()

        # set title text objects
        self._set_sentence_objects_list_for_title_sentences()

        # set quote content embeddings
        self._fetch_and_set_quote_content_embeddings()

        return PreprocessedContents(
            title_text=self.title_text_objects_list,
            normal_text=self.sentence_objects_list,
            media=self.media_content_list,
            embedded_content=self.embedded_content_list,
            quoted_content=self.quoted_content_list
        )

    def _split_content(self):
        ''' Splits the given content into 4 lists

        * list of title text
        * list of normal text - each text is a
            paragraph or a block of sentences
        * list of media content (images/gifs)
        * list of embedded content
        '''
        for content in self.content_list:
            if content.content_type == ContentType.TEXT:
                if content.is_important_text() \
                        and len(content.text_string) < self.MAX_TITLE_LENGTH:
                    self.title_text_content_list.append(content)
                else:
                    self.normal_text_content_list.append(content)

            elif content.content_type == ContentType.IMAGE:
                self.media_content_list.append(content)

            elif content.content_type == ContentType.QUOTE:
                self.quoted_content_list.append(content)

            elif content.content_type.is_embedded_content():
                self.embedded_content_list.append(content)

    def _strip_numbering_from_title_text(self):
        for title_text in self.title_text_content_list:
            title_text.text_string \
                = self._strip_numbering_prefix_from_text(
                    title_text.text_string)

    def _strip_numbering_prefix_from_text(self, text):
        numbered_prefix_list = re.findall('^[0-9]+[:,.,)]*', text)
        if numbered_prefix_list == []:
            return text.strip()  # strip spaces and tabs frome ends
        numbered_prefix = numbered_prefix_list[0]

        return text[len(numbered_prefix):].strip()

    def _set_sentence_objects_list_for_title_sentences(self):
        ''' sets the list of title sentences objects'''
        # initialize an empty list
        self.title_text_objects_list = list()

        # collect and retrieve all embeddings at once
        embeddings = self.sentence_embedding_model.encode([
            text.text_string for text in self.title_text_content_list
        ])

        # instantiate and append the sentence object
        for title_text, embedding in zip(
                self.title_text_content_list, embeddings):
            self.title_text_objects_list.append(
                SentenceWithAttributes(
                    title_text.text_string,
                    title_text.content_index,
                    0,
                    0,
                    None,
                    embedding
                )
            )

    def _summarize_text_content(self):
        '''
        Applies text summarization to the combined text in the webpage
        '''
        # aggregate all text from the webpage
        webpage_text = ""
        for text in self.normal_text_content_list:
            webpage_text += text.text_string
            # if it doesnt end with a fullstop -
            # manually end it with a full stop
            if text.text_string[-1] != '.':
                webpage_text += '.'
            # add a whitespace regardless
            webpage_text += " "

        # sentence tokenize to get list of summary sentences
        self.summarized_text = sent_tokenize(
            self.text_summarizer.summarize_text(webpage_text))

        # store counts of each content type for future use
        self.count_of_summary_sentences = len(self.summarized_text)
        self.count_of_normal_text = len(self.normal_text_content_list)

    def _get_alphanumeric_tokens(self, word_tokenized_text):
        ''' filters and returns alphanumeric tokens'''
        return [token.lower() for token in
                word_tokenized_text if token.isalnum()]

    def _get_tokenized_and_text_object_from_index(
            self, normal_text_index):
        # if index out of bounds return
        if normal_text_index >= self.count_of_normal_text:
            return None

        # first sentence tokenize the text
        sentence_tokenized_text = sent_tokenize(
            self.normal_text_content_list[normal_text_index].text_string
        )

        # word tokenize
        word_tokenized_text = [
            word_tokenize(text) for text in sentence_tokenized_text]

        # only retain alphanumeric tokens
        return [self._get_alphanumeric_tokens(text) for
                text in word_tokenized_text]

    def _get_tokenized_summary_sentence_from_index(
            self, summarized_text_index):
        # if index out of bounds return
        if summarized_text_index >= self.count_of_summary_sentences:
            return None
        # only word tokenize since it is a single sentence already
        word_tokenized_text = word_tokenize(
            self.summarized_text[summarized_text_index])

        # retain and set alphanumeric characters
        return self._get_alphanumeric_tokens(word_tokenized_text)

    def _get_sentence_object_for_summarized_sentence(
            self,
            summarized_text_index,
            normal_text_index,
            sentence_index_in_para,
            sentence_weight):
        ''' instantiates/initializes and returns a sentence object'''
        return SentenceWithAttributes(
            self.summarized_text[summarized_text_index],
            self.normal_text_content_list[normal_text_index].content_index,
            sentence_index_in_para,
            sentence_weight,
            self.normal_text_content_list[
                normal_text_index].font_style,
            self.summarized_text_embeddings[
                summarized_text_index]
        )

    def _tokenized_text_object_has_sentence(
            self, text_object, sentence):
        '''
        Given the index of the text object and
        the index of the summarized sentence -
        checks if the summarized sentence lies
        in the text object at that index

        note that the text object is a block/collection
        of sentences and we need to search among all
        sentences present in that block
        text_object : sentence and wordtokenized text object:
        sentence : word_tokenized sentence
        '''
        return sentence in text_object

    def _make_sentence_objects(self):
        ''' Assembles the sentence objects with required attributes

        Note that the summarized text is a subsequence of the
        webpage text. We use this fact to assemble the sentence
        objects

        Maintain two running indices through each list
        i: running index in normal text content
        j: running index in summarized text content
        - When the text is present at some indices i,j in the lists we
            increment both i and j
        - Else just increment i
        '''
        self.sentence_objects_list = list()

        # list of lists of tokenized words
        tokenized_and_cleaned_text_object = []
        # list of tokenized words
        tokenized_and_cleaned_summary_sentence = []

        self.running_index_in_normal_text_content = 0
        tokenized_and_cleaned_text_object \
            = self._get_tokenized_and_text_object_from_index(0)

        self.running_index_in_summarized_text = 0
        tokenized_and_cleaned_summary_sentence \
            = self._get_tokenized_summary_sentence_from_index(0)

        # encode all sentences together - reduces latency
        self.summarized_text_embeddings = self.sentence_embedding_model.encode(
            self.summarized_text)

        # to assign different indices for sentences in paragraph
        sentence_index_in_paragraph = 0
        # step size -based on number of sentences in para
        if tokenized_and_cleaned_text_object:
            step_size_for_sentence_index_in_paragraph \
                = 1 / len(tokenized_and_cleaned_text_object)

        while self.running_index_in_summarized_text \
                < self.count_of_summary_sentences and \
                self.running_index_in_normal_text_content\
                < self.count_of_normal_text:

            if self._tokenized_text_object_has_sentence(
                    tokenized_and_cleaned_text_object,
                    tokenized_and_cleaned_summary_sentence):

                self.sentence_objects_list.append(
                    self._get_sentence_object_for_summarized_sentence(
                        self.running_index_in_summarized_text,
                        self.running_index_in_normal_text_content,
                        sentence_index_in_paragraph,
                        step_size_for_sentence_index_in_paragraph
                    )
                )
                self.running_index_in_summarized_text += 1
                sentence_index_in_paragraph += 1
                # fetch and set the new tokenized summary sentence
                tokenized_and_cleaned_summary_sentence =\
                    self._get_tokenized_summary_sentence_from_index(
                        self.running_index_in_summarized_text
                    )
            else:
                # only increment when the sentence is not found
                # in the paragraph/block of normal text sentences
                self.running_index_in_normal_text_content += 1
                # fetch and set the new tokenized text object
                tokenized_and_cleaned_text_object \
                    = self._get_tokenized_and_text_object_from_index(
                        self.running_index_in_normal_text_content)

                sentence_index_in_paragraph = 0
                # in case the index goes out of bounds None would
                # have been returned and the loop will terminate
                # on the next iteration - this is a safeguard
                # agaisnst that scenario
                if tokenized_and_cleaned_text_object is not None:
                    step_size_for_sentence_index_in_paragraph \
                        = 1 / len(tokenized_and_cleaned_text_object)

    def get_condensed_image_description(self, image_description):
        ''' Concatenates the various image descriptions into
        a single string and returns them
        '''
        # can be amended to display extra fields if required
        return image_description["label"] + \
            ' '.join(image_description["entities"])

    def get_condensed_image_attributes(self, image):
        ''' Combines the image attributes present
        and returns them accordingly
        '''
        # will be amended to add more information once OCR label
        # field is added to Image object
        if not image.img_caption:
            return ""
        return image.img_caption

    def _fetch_media_embeddings(self):
        image_describer = ImageDescriptionRetriever()
        self.image_descriptions \
            = image_describer.get_description_for_images(
                [media.img_url for media in self.media_content_list]
            )
        # fetch all image descriptions+embeddings together - reduces latency
        self.media_description_embeddings \
            = self.sentence_embedding_model.encode([
                self.get_condensed_image_description(image_description)
                for image_description in self.image_descriptions
            ])

        self.media_attribute_embeddings \
            = self.sentence_embedding_model.encode([
                self.get_condensed_image_attributes(image) for image in
                self.media_content_list
            ])

    def _fetch_and_set_quote_content_embeddings(self):
        quote_embeddings = self.sentence_embedding_model.encode(
            [quote.q_content for quote in self.quoted_content_list]
        )

        for quote, embedding in zip(
                self.quoted_content_list, quote_embeddings):
            quote.embedding = embedding

    def _add_media_description_and_attribute_embeddings(self):
        ''' fills the img_description_embedding/attribute
        field in the media objects
        '''
        self._fetch_media_embeddings()

        for media_content,\
            media_description_embedding,\
            media_attribute_embedding,\
            image_description in zip(
                self.media_content_list,
                self.media_description_embeddings,
                self.media_attribute_embeddings,
                self.image_descriptions):

            media_content.img_description_embedding \
                = media_description_embedding

            media_content.img_attribute_embedding \
                = media_attribute_embedding

            media_content.has_text_on_image \
                = image_description["has_caption"]

            media_content.image_colors \
                = image_description["image_colors"]
